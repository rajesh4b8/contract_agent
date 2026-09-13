from fastapi import (
    APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Query,
    Depends, Request,
)
from backend.governance.rbac import Permission, get_current_tenant, requires_permission
from fastapi.responses import StreamingResponse
from backend.application.services.document_processing_service import DocumentServiceFactory
from backend.domain.entities import DocumentProcessingRequest
from backend.llm_manager import LLMManager
from backend.shared.config.models import DEFAULT_MODEL_ID
from backend.infrastructure.audit_logger import AuditLogger, AuditEventType, audit_log
from backend.infrastructure.content_validator import ContentValidationService
from backend.infrastructure.error_tracker import ErrorTracker, ErrorCategory, ErrorSeverity, error_tracking_context
from backend.shared.errors import classify_llm_error, describe_llm_error, raise_if_provider_error
from backend.shared.debug import note, trace_step
from backend.agents.chunking_agent import ChunkingAgent
from backend.domain.matter import (
    counterparty_from_parties,
    parse_status,
    source_sha256,
    suggest_title,
)
from backend.infrastructure.chunking.storage_service import ChunkStorageService
from backend.infrastructure.matter_repository import (
    DuplicateSource,
    MatterClosed,
    MatterNotFound,
    MatterRepository,
)
import os
import uuid
import json
import logging
from typing import Optional

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/documents", tags=["documents"])

# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager

@router.get("/debug/contracts", dependencies=[Depends(requires_permission(Permission.VIEW_AUDIT))])
async def debug_contracts(tenant_id: str = Depends(get_current_tenant)):
    """Debug endpoint to see all contracts.

    Scoped to the caller's tenant, like everything else. `GET /api/matters` is
    what the application navigates by; this stays a debugging aid, and is still
    gated on VIEW_AUDIT, which the default LEGAL_REVIEWER role does not hold.
    """
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        repo = Neo4jContractRepository()
        
        query = """
        MATCH (c:Contract {tenant_id: $tenant_id})
        RETURN c.file_id as contract_id, 
               c.contract_type as contract_type,
               c.summary as summary,
               c.source as source
        ORDER BY c.upload_date DESC
        """
        
        result = repo.graph.query(query, {"tenant_id": tenant_id})
        
        contracts = []
        for row in result:
            contracts.append({
                "contract_id": row["contract_id"],
                "contract_type": row["contract_type"],
                "summary": row["summary"][:100] + "..." if row["summary"] and len(row["summary"]) > 100 else row["summary"],
                "source": row["source"]
            })
        
        return {
            "total_contracts": len(contracts),
            "contracts": contracts
        }
        
    except Exception as e:
        logger.error(f"Debug contracts failed: {e}")
        return {"error": str(e)}

@router.get("/debug/contract-types", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def debug_contract_types(tenant_id: str = Depends(get_current_tenant)):
    """Debug endpoint to see contract type distribution"""
    try:
        from backend.infrastructure.contract_repository import Neo4jContractRepository
        repo = Neo4jContractRepository()
        
        query = """
        MATCH (c:Contract {tenant_id: $tenant_id})
        RETURN c.contract_type as contract_type, count(*) as count
        ORDER BY count DESC
        """
        
        result = repo.graph.query(query, {"tenant_id": tenant_id})
        
        return {
            "contract_types": [{"type": row["contract_type"], "count": row["count"]} for row in result]
        }
        
    except Exception as e:
        logger.error(f"Debug contract types failed: {e}")
        return {"error": str(e)}

def _duplicate_response(filename: str, model: str, twin: dict) -> dict:
    """The answer when these exact bytes are already a version.

    Built in two places: the pre-flight lookup, which saves two minutes of
    pointless extraction, and the uniqueness constraint, which is what actually
    decides it when two uploads race.
    """
    return {
        "message": "Duplicate file detected",
        "filename": filename,
        "status": "duplicate",
        "contract_id": twin.get("version_id"),
        "existing_contract_id": twin.get("version_id"),
        "matter_ref": twin.get("matter_ref"),
        "version": twin.get("n"),
        "details": f"Exactly matches version {twin.get('n')} of {twin['matter_ref']}",
        "model_used": model,
        "action": "skipped",
    }


async def _filing_proposal(repo, contract_id: str, tenant_id: str,
                           filename: str = "") -> dict:
    """What the confirmation card starts out saying.

    The upload pipeline already extracts the counterparty, the contract type and
    the dates — asking the reviewer to type them again would be asking them to
    re-do work the model has done. Every field here is a guess they can correct;
    the counterparty in particular, since nothing in the data yet says which
    party is *us*.
    """
    try:
        stored = await repo.get_contract_by_id(contract_id, tenant_id)
    except Exception as e:
        logger.warning(f"Could not read back {contract_id} for the filing card: {e}")
        stored = None

    if not stored:
        return {
            "title": suggest_title(None, None, filename),
            "counterparty": "",
            "contract_type": "",
            "effective_date": None,
            "end_date": None,
            "parties": [],
        }

    counterparty = counterparty_from_parties(stored.get("parties"))
    contract_type = stored.get("contract_type") or ""
    return {
        "title": suggest_title(contract_type, counterparty, filename),
        "counterparty": counterparty,
        "contract_type": contract_type,
        "effective_date": stored.get("effective_date"),
        "end_date": stored.get("end_date"),
        "parties": stored.get("parties") or [],
        "summary": (stored.get("summary") or "")[:400],
    }


@router.post("/upload", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
@audit_log(AuditEventType.DOCUMENT_UPLOAD, "upload_pdf")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    # The tenant is the caller's, never the URL's. Upload used to read it from a
    # query parameter the frontend has never set, so every contract landed in
    # `default-tenant` while its redlines were written under the tenant in the
    # `X-Tenant-ID` header — the same document filed twice, in two places.
    tenant_id: str = Depends(get_current_tenant),
    # Accepted from the multipart body *and* the query string, because both
    # callers are real: the browser sends a FormData field, `scripts/` sends a
    # query parameter. The endpoint only ever declared the query form, so the
    # model dropdown in the UI has silently done nothing since it was added and
    # every upload used DEFAULT_MODEL_ID — 50-130s a call, which is very likely
    # the "uploads take a long time" report. The body wins when both are given.
    model: Optional[str] = Form(default=None, description="LLM model to use for processing"),
    model_param: Optional[str] = Query(default=None, alias="model"),
    # Present for "upload a new round into this matter", absent for
    # "new contract" — in which case the document is stored unfiled and the
    # caller confirms it into a matter afterwards.
    matter_ref: Optional[str] = Form(default=None, description="File this round under an existing matter"),
    matter_ref_param: Optional[str] = Query(default=None, alias="matter_ref"),
    enable_enhanced: bool = Query(default=False, description="Enable enhanced processing with sections/clauses"),
    llm_mgr: LLMManager = Depends(get_llm_manager)
):
    """
    Upload and process PDF contract
    - Validates file type and size
    - Processes using PDF processing agent
    - Returns processing status

    Two paths, distinguished by `matter_ref`:

    * **into a matter** — the reviewer opened `MSA-2026-0042` and clicked
      *Upload new round*. Nothing is inferred and nothing is asked.
    * **new contract** — no matter yet. The document is extracted and stored as
      an unfiled version, and the response carries a pre-filled proposal
      (counterparty, type, dates) for the caller to confirm. The reference
      number is allocated at that confirmation, so a document that never parsed
      — or a card the user cancels — burns no number and leaves no gap.
    """

    model = model or model_param or DEFAULT_MODEL_ID
    matter_ref = (matter_ref or matter_ref_param or "").strip() or None

    logger.info(f"=== UPLOAD START: {file.filename if file else 'NO FILE'} ===")
    
    # Initialize services
    audit_logger = AuditLogger()
    validator = ContentValidationService()
    
    with error_tracking_context(
        operation="document_upload",
        category=ErrorCategory.FILE_ERROR,
        severity=ErrorSeverity.HIGH,
        resource_id=file.filename if file else "unknown",
        metadata={"model": model}
    ) as error_context:
        try:
            note("upload", "received", filename=file.filename, model=model, tenant=tenant_id)

            # Input validation
            logger.info(f"Step 1: Input validation for file: {file.filename}")
            with trace_step("upload", "validate_filename", filename=file.filename):
                if not file.filename:
                    logger.error("No filename provided")
                    raise HTTPException(status_code=400, detail="No filename provided")

                if not file.filename.lower().endswith('.pdf'):
                    logger.error(f"Invalid file type: {file.filename}")
                    raise HTTPException(status_code=400, detail="Only PDF files are supported")

            # Check file size (50MB limit)
            logger.info("Step 2: Reading file content")
            with trace_step("upload", "read_file") as _step:
                file_content = await file.read()
                _step.set(bytes=len(file_content))
            logger.info(f"File size: {len(file_content)} bytes")

            # Validate file metadata
            validation_data = {
                "filename": file.filename,
                "file_size": len(file_content)
            }

            with trace_step("upload", "validate_metadata") as _step:
                validation_result = validator.validate_file_upload(validation_data)
                _step.set(valid=validation_result["is_valid"])

            if not validation_result["is_valid"]:
                audit_logger.log_event(
                    event_type=AuditEventType.VALIDATION_FAILURE,
                    resource_id=file.filename,
                    action="file_validation",
                    status="failure",
                    error_details=json.dumps(validation_result)
                )
                raise HTTPException(
                    status_code=400, 
                    detail=f"Validation failed: {validation_result['summary']}"
                )

            logger.info("Step 3: Resolving the destination matter")
            try:
                from backend.infrastructure.contract_repository import Neo4jContractRepository
                repo = Neo4jContractRepository()
                matters = MatterRepository()
                logger.info("Repository initialized successfully")
            except Exception as repo_error:
                logger.error(f"Repository initialization failed: {repo_error}")
                raise

            # Checked before anything expensive happens. A closed matter or a
            # reference that does not exist should cost the reviewer a second,
            # not two minutes of extraction and embedding.
            if matter_ref:
                with trace_step("upload", "resolve_matter", matter_ref=matter_ref) as _step:
                    destination = matters.get_matter(tenant_id, matter_ref)
                    _step.set(found=bool(destination))
                if destination is None:
                    # 404 for an unknown reference and for another tenant's
                    # alike: 403 would confirm that it exists.
                    raise HTTPException(status_code=404, detail=f"No matter {matter_ref}")
                if not parse_status(destination.get("status")).accepts_new_versions:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{matter_ref} is closed. Reopen it before uploading a new round.",
                    )

            # Save file temporarily
            logger.info("Step 4: Saving file temporarily")
            temp_filename = f"{uuid.uuid4().hex}_{file.filename}"
            temp_path = f"/tmp/{temp_filename}"
            
            try:
                with trace_step("upload", "save_temp", bytes=len(file_content)):
                    with open(temp_path, "wb") as temp_file:
                        temp_file.write(file_content)
                logger.info(f"File saved successfully: {file.filename} -> {temp_path}")
            except Exception as save_error:
                logger.error(f"Failed to save file: {save_error}")
                raise

            # Extract full text for storage
            logger.info("Step 5: Extracting text from PDF")
            try:
                from backend.infrastructure.text_extractors import TextExtractionService
                text_extractor = TextExtractionService()
                # Worth knowing when reading the timeline: the PDF is extracted a
                # second time inside the processing agent's `extract_text` node.
                with trace_step("upload", "pdf_extract") as _step:
                    full_text = text_extractor.extract_with_fallback(temp_path)
                    _step.set(chars=len(full_text))
                logger.info(f"Text extraction completed. Length: {len(full_text)} characters")

                # The one automatic case in this increment: byte-identical
                # source. Not a judgement call, so nothing is asked — no version
                # is created and the user lands on the matter that already holds
                # it. This is what stops a double-click producing v2 = v1.
                #
                # It replaces a check that matched the filename against
                # `file_id`, which is generated as `UPLOADED_{random}_{date}` and
                # never contains it — so the check never fired, and every
                # re-upload of a revised contract silently created a second,
                # unrelated Contract, orphaning the previous round's redline
                # decisions. It also ran across every tenant in the database.
                with trace_step("upload", "duplicate_check") as _step:
                    digest = source_sha256(full_text)
                    _step.set(sha256=digest[:12])
                    try:
                        twin = matters.version_by_source_hash(tenant_id, digest)
                    except Exception as query_error:
                        # A failed lookup must not block the upload; the worst
                        # case is a duplicate version the user can see and act on.
                        logger.error(f"Duplicate check failed: {query_error}")
                        twin = None
                    _step.set(duplicate=bool(twin))

                if twin and twin.get("matter_ref"):
                    note("upload", "duplicate_skipped", filename=file.filename,
                         matter_ref=twin["matter_ref"])
                    os.path.exists(temp_path) and os.remove(temp_path)
                    return _duplicate_response(file.filename, model, twin)

                if twin and not twin.get("matter_ref"):
                    # The same bytes were uploaded but never filed — a cancelled
                    # or double-clicked confirmation card. Re-offer that version
                    # instead of storing the document a second time.
                    note("upload", "unfiled_duplicate", filename=file.filename,
                         contract_id=twin["version_id"])
                    os.path.exists(temp_path) and os.remove(temp_path)
                    return {
                        "message": "PDF processing completed",
                        "filename": file.filename,
                        "status": "success",
                        "contract_id": twin["version_id"],
                        "needs_filing": True,
                        "proposal": await _filing_proposal(repo, twin["version_id"], tenant_id,
                                                           file.filename),
                        "details": "This document was already uploaded and is waiting to be filed.",
                        "model_used": model,
                        "validation_passed": True,
                    }

                # Validate content quality
                with trace_step("upload", "content_validate", chars=len(full_text)) as _step:
                    content_validation = validator.validate({"full_text": full_text})
                    _step.set(has_errors=content_validation["has_errors"])
                
                if content_validation["has_errors"]:
                    audit_logger.log_event(
                        event_type=AuditEventType.VALIDATION_FAILURE,
                        resource_id=file.filename,
                        action="content_validation",
                        status="failure",
                        error_details=json.dumps(content_validation)
                    )
                    logger.warning(f"Content validation issues: {content_validation['summary']}")
                else:
                    logger.info(f"Content validation passed: {len(full_text)} characters")
                
                # Step 5.5: Enhanced Intelligent Chunking with Embeddings
                logger.info("Step 5.5: Enhanced intelligent chunking with embeddings")
                # This block dominates an upload's wall clock: chunk embedding makes
                # one network call per chunk. The per-batch `chunking.embed` progress
                # events come from the embedding optimizer underneath it, so the
                # panel shows movement instead of a minute of silence.
                try:
                    # Initialize embedding service
                    from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService
                    embedding_service = GeminiEmbeddingService()

                    chunking_agent = ChunkingAgent(embedding_service)
                    contract_id = file.filename.replace('.pdf', '')

                    # Try async enhanced chunking first
                    try:
                        with trace_step("upload", "chunking", chars=len(full_text)) as step:
                            chunking_result = await chunking_agent.process_document(
                                document_id=contract_id,
                                content=full_text,
                                metadata={
                                    "filename": file.filename,
                                    "document_type": "contract",
                                    "file_size": len(full_text)
                                }
                            )

                            if not chunking_result["success"]:
                                logger.warning("Enhanced chunking failed, falling back to sync method")
                                raise Exception("Enhanced chunking failed")

                            _plan = chunking_result.get('plan') or {}
                            _strategy = _plan.get('strategy_type') if isinstance(_plan, dict) else getattr(_plan, 'strategy_type', None)
                            _quality = chunking_result['quality_assessment']['overall_quality']
                            step.set(
                                chunks=chunking_result['chunk_count'],
                                strategy=str(_strategy),
                                quality=round(_quality, 2),
                            )
                            logger.info(f"Enhanced chunking completed: {chunking_result['chunk_count']} chunks, "
                                      f"strategy: {_strategy}, "
                                      f"quality: {_quality:.2f}")

                            # Log document analysis insights
                            doc_analysis = chunking_result.get('document_analysis', {})
                            if doc_analysis.get('is_legal_document'):
                                logger.info(f"Legal document detected - sections: {doc_analysis.get('section_count', 0)}, "
                                          f"clauses: {doc_analysis.get('clause_count', 0)}")

                    except Exception as async_error:
                        logger.warning(f"Async chunking failed: {async_error}, trying sync method")
                        note("upload", "chunking_fallback", reason=str(async_error))

                        # Fallback to synchronous chunking
                        with trace_step("upload", "chunking_sync", chars=len(full_text)) as step:
                            chunking_result = chunking_agent.process_document_sync(
                                content=full_text,
                                metadata={
                                    "filename": file.filename,
                                    "document_type": "contract"
                                }
                            )

                            # Store chunks using existing schema for backward compatibility
                            storage_service = ChunkStorageService()
                            chunk_ids = storage_service.store_chunks(
                                contract_id=contract_id,
                                chunks=chunking_result["chunks"]
                            )
                            step.set(
                                chunks=len(chunk_ids),
                                strategy=str(chunking_result['strategy_used']),
                                quality=round(chunking_result['quality_score'], 2),
                            )

                            logger.info(f"Sync chunking completed: {len(chunk_ids)} chunks, "
                                      f"strategy: {chunking_result['strategy_used']}, "
                                      f"quality: {chunking_result['quality_score']:.2f}")

                except Exception as chunking_error:
                    logger.warning(f"All chunking methods failed, continuing without chunking: {chunking_error}")
                    note("upload", "chunking_skipped", reason=str(chunking_error))
                    # System continues normally without chunking - no breaking changes
                
            except Exception as extract_error:
                logger.error(f"Text extraction failed: {extract_error}")
                raise

            # Create processing request
            logger.info("Step 6: Creating processing request")
            try:
                processing_request = DocumentProcessingRequest(
                    file_path=temp_path,
                    filename=file.filename,
                    tenant_id=tenant_id,
                    processing_options={
                        "model": model, 
                        "full_text": full_text,
                        "enable_enhanced": enable_enhanced
                    }
                )
                logger.info("Processing request created successfully")
            except Exception as request_error:
                logger.error(f"Failed to create processing request: {request_error}")
                raise

            # Process synchronously with error handling
            logger.info(f"Step 7: Starting document processing for: {file.filename}")
            try:
                # Check if enhanced processing is requested
                enable_enhanced = processing_request.processing_options.get("enable_enhanced", False)
                
                if enable_enhanced:
                    with trace_step("upload", "process_enhanced", model=model):
                        # Use enhanced processor with sections/clauses
                        from backend.factories.document_processor_factory import DocumentProcessorFactory
                        # get_model_by_name, not agents[model]: the raw lookup raises a
                        # bare KeyError whose whole message is the model id, which
                        # surfaced as "Processing failed: 'gemini-flash'".
                        processor = DocumentProcessorFactory.create_processor(
                            "full", llm_mgr.get_model_by_name(model)
                        )
                        # Ensure tenant_id is passed in options
                        processing_request.processing_options["tenant_id"] = tenant_id
                        result = await processor.process_document(temp_path, processing_request.processing_options)
                    
                        # Convert to expected format
                        if result["status"] == "success":
                            result = {
                                "status": "success",
                                "contract_id": result["contract_id"],
                                "final_result": f"SUCCESS: Enhanced processing completed. Sections: {result['sections_extracted']}, Clauses: {result['clauses_extracted']}, CUAD: {result['cuad_classifications']}"
                            }
                else:
                    # The PDF agent: a second text extraction, then the model call
                    # that pulls out parties and dates, then the Neo4j write.
                    with trace_step("upload", "process_pdf", model=model) as step:
                        document_service = DocumentServiceFactory.create_service(llm_mgr)
                        result = await document_service.process_pdf_upload(processing_request)
                        step.set(status=str(result.get("status")))

                logger.info(f"Document processing completed successfully: {result}")
            except Exception as proc_error:
                logger.error(f"Document processing failed: {str(proc_error)}")
                logger.error(f"Processing error type: {type(proc_error).__name__}")
                import traceback
                logger.error(f"Processing traceback: {traceback.format_exc()}")
                
                # Track processing error
                audit_logger.log_event(
                    event_type=AuditEventType.PROCESSING_ERROR,
                    resource_id=file.filename,
                    action="document_processing",
                    status="failure",
                    error_details=str(proc_error)
                )
                
                # Return error as JSON instead of raising exception. `details`
                # is the only field the upload panel shows, so it carries the
                # explanation: for a model failure that is "the day's Gemini
                # quota is gone, pick a Free · model", not a stack trace.
                note(
                    "upload",
                    "processing_failed",
                    error_type=type(proc_error).__name__,
                    error=str(proc_error),
                )
                llm_error = classify_llm_error(proc_error, model)
                response = {
                    "message": "PDF processing failed",
                    "filename": file.filename,
                    "status": "error",
                    "contract_id": None,
                    "details": llm_error.message if llm_error else f"Processing error: {proc_error}",
                    "model_used": model,
                    "error_type": type(proc_error).__name__
                }
                if llm_error:
                    response["error_kind"] = llm_error.failure.value
                    response["provider"] = llm_error.provider
                    response["retry_after"] = llm_error.retry_after
                return response
            
            logger.info(f"PDF processing completed for {file.filename}: {result['status']}")
            
            # Extract contract ID from result details if not directly available
            contract_id = result.get("contract_id")
            if not contract_id and "SUCCESS: Contract stored with ID:" in result.get("final_result", ""):
                contract_id = result["final_result"].split("SUCCESS: Contract stored with ID:")[-1].strip()
            
            # Log successful upload
            audit_logger.log_event(
                event_type=AuditEventType.DOCUMENT_UPLOAD,
                resource_id=contract_id or file.filename,
                action="upload_completed",
                status="success",
                metadata={"filename": file.filename, "model": model}
            )
            
            response = {
                "message": "PDF processing completed",
                "filename": file.filename,
                "status": result["status"],
                "contract_id": contract_id,
                "details": result.get("final_result", ""),
                "model_used": model,
                "validation_passed": True
            }

            # Everything past this point needs a stored contract to hang off.
            # A document that was skipped, or needed manual review, has none —
            # and filing it would be filing nothing.
            if contract_id:
                try:
                    matters.record_version(
                        tenant_id, contract_id,
                        source_sha256=digest, filename=file.filename,
                    )
                except DuplicateSource as clash:
                    # The uniqueness constraint, not the pre-flight lookup, is
                    # what decides this under concurrency: two uploads of the
                    # same PDF can both pass the check at the start and only
                    # meet here, two minutes later. The one that lost reports
                    # the matter that holds the bytes.
                    twin = clash.existing or {}
                    note("upload", "duplicate_rejected", filename=file.filename,
                         contract_id=contract_id,
                         matter_ref=twin.get("matter_ref"))
                    # This upload's Contract node never became a version, so
                    # nothing can reach it. Marked, not deleted, so the migration
                    # does not later file it as a matter of its own.
                    if twin.get("version_id"):
                        matters.mark_superseded(tenant_id, contract_id, twin["version_id"])
                    if twin.get("matter_ref"):
                        return _duplicate_response(file.filename, model, twin)
                    # The winner is itself still unfiled — send the reviewer to
                    # its confirmation card rather than to a second one.
                    return {
                        **response,
                        "contract_id": twin.get("version_id") or contract_id,
                        "needs_filing": True,
                        "proposal": await _filing_proposal(
                            repo, twin.get("version_id") or contract_id, tenant_id,
                            file.filename,
                        ),
                        "details": "This document was already uploaded and is waiting "
                                   "to be filed.",
                    }
                except Exception as record_error:
                    # The contract is stored but is not a version, so it can be
                    # neither filed nor confirmed. Saying "success" here would
                    # hand the UI a card whose confirmation can only 404.
                    logger.error(f"Could not record {contract_id} as a version: "
                                 f"{record_error}")
                    note("upload", "record_version_failed", contract_id=contract_id,
                         error_type=type(record_error).__name__, error=str(record_error))
                    return {
                        **response,
                        "status": "error",
                        "needs_filing": False,
                        "details": f"The document was extracted but could not be stored "
                                   f"as a version, so it cannot be filed: {record_error}",
                    }

                if matter_ref:
                    try:
                        filed = matters.attach_version(tenant_id, matter_ref, contract_id)
                    except MatterClosed:
                        raise HTTPException(
                            status_code=409,
                            detail=f"{matter_ref} is closed. Reopen it before uploading "
                                   f"a new round.",
                        )
                    except MatterNotFound:
                        raise HTTPException(status_code=404, detail=f"No matter {matter_ref}")
                    except Exception as filing_error:
                        # The version exists; only the link to the matter is
                        # missing. That is recoverable, and must not be reported
                        # as a filed round — the page would close the uploader
                        # and show a version that is not there.
                        logger.error(f"Could not file {contract_id} under {matter_ref}: "
                                     f"{filing_error}")
                        note("upload", "filing_failed", contract_id=contract_id,
                             matter_ref=matter_ref, error=str(filing_error))
                        return {
                            **response,
                            "status": "error",
                            "needs_filing": False,
                            "details": f"The document was stored but could not be added to "
                                       f"{matter_ref}: {filing_error}. Try uploading it again.",
                        }

                    response["matter_ref"] = filed["matter_ref"]
                    response["version"] = filed["n"]
                    note("upload", "filed", contract_id=contract_id,
                         matter_ref=filed["matter_ref"], version=filed["n"])
                else:
                    # Unfiled on purpose. The caller confirms the pre-filled
                    # card, and only then is a reference number allocated.
                    # `needs_filing` is set only now, because the version it
                    # refers to is only now known to exist.
                    response["needs_filing"] = True
                    response["proposal"] = await _filing_proposal(
                        repo, contract_id, tenant_id, file.filename
                    )

            note("upload", "completed", contract_id=contract_id, status=result["status"],
                 matter_ref=response.get("matter_ref"))

            return response
        
        except HTTPException:
            logger.error(f"HTTP Exception in upload: {file.filename if file else 'unknown'}")
            raise
        except Exception as e:
            logger.error(f"=== UPLOAD FAILED: {file.filename if file else 'unknown'} ===")
            logger.error(f"Error: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
            # Cleanup temp file if it exists
            if 'temp_path' in locals() and temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    logger.info(f"Cleaned up temp file: {temp_path}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup temp file: {cleanup_error}")
                    
            # A model failure is not a 500 from this service: answer with its
            # own status (429 for a spent quota, 503 for a key problem) and the
            # message that says what to do about it.
            raise_if_provider_error(e, model)
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
        finally:
            logger.info(f"=== UPLOAD END: {file.filename if file else 'unknown'} ===")

@router.post("/upload-stream", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
async def upload_pdf_stream(
    file: UploadFile = File(...),
    tenant_id: str = Depends(get_current_tenant),
    model: Optional[str] = Form(default=None, description="LLM model to use for processing"),
    model_param: Optional[str] = Query(default=None, alias="model"),
    llm_mgr: LLMManager = Depends(get_llm_manager)
):
    """
    Upload and process PDF with streaming response
    Similar to existing /run/ endpoint pattern
    """
    
    model = model or model_param or DEFAULT_MODEL_ID

    try:
        # Validation (same as above)
        if not file.filename or not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Invalid file")
        
        file_content = await file.read()
        if len(file_content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large")
        
        # Save file temporarily
        temp_filename = f"{uuid.uuid4().hex}_{file.filename}"
        temp_path = f"/tmp/{temp_filename}"
        
        with open(temp_path, "wb") as temp_file:
            temp_file.write(file_content)
        
        # Create processing request
        processing_request = DocumentProcessingRequest(
            file_path=temp_path,
            filename=file.filename,
            tenant_id=tenant_id,
            processing_options={"model": model}
        )
        
        # Stream processing results (similar to existing /run/ endpoint)
        async def stream_processing():
            try:
                # Create service with injected LLM manager
                document_service = DocumentServiceFactory.create_service(llm_mgr)
                # Get LLM and create agent
                llm = document_service._get_llm_for_model(model)
                from backend.agents.pdf_processing_agent import PDFAgentFactory
                pdf_agent = PDFAgentFactory.create_agent(llm)
                
                # Create processing message
                from langchain_core.messages import HumanMessage
                processing_message = HumanMessage(content=f"""
                Process this PDF contract document:
                
                File path: {temp_path}
                Filename: {file.filename}
                
                Please:
                1. Extract text from the PDF
                2. Analyze if it's a valid contract
                3. Extract structured contract information
                4. Validate the data quality
                5. Store the contract if validation passes
                """)
                
                messages = [processing_message]
                
                # Create initial state for streaming
                initial_state = {
                    "file_path": temp_path,
                    "tenant_id": tenant_id,
                    "model_id": model,
                    "messages": messages,
                    "extracted_text": None,
                    "contract_data": None,
                    "processing_result": None
                }
                
                # Stream results (same pattern as existing system)
                async for chunk in pdf_agent.astream(initial_state, stream_mode=["messages", "updates"]):
                    if chunk[0] == "messages":
                        message = chunk[1]
                        if hasattr(message[0], 'tool_calls') and len(message[0].tool_calls) > 0:
                            for tool in message[0].tool_calls:
                                if tool.get('name'):
                                    tool_calls_content = json.dumps(tool)
                                    yield f"data: {json.dumps({'content': tool_calls_content, 'type': 'tool_call'})}\n\n"
                        
                        if hasattr(message[0], 'content') and message[0].content:
                            yield f"data: {json.dumps({'content': message[0].content, 'type': 'ai_message'})}\n\n"
                
                # Final completion message
                yield f"data: {json.dumps({'content': f'PDF processing completed for {file.filename}', 'type': 'completion'})}\n\n"
                yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"
                
            except Exception as e:
                logger.error(f"Streaming PDF processing failed: {e}", exc_info=True)
                error_msg = describe_llm_error(e, model, fallback=f"Processing failed: {e}")
                yield f"data: {json.dumps({'content': error_msg, 'type': 'error'})}\n\n"
                yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"
            finally:
                # Cleanup
                document_service._cleanup_file(temp_path)
        
        return StreamingResponse(
            stream_processing(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Streaming PDF upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def get_upload_status(llm_mgr: LLMManager = Depends(get_llm_manager)):
    """Get system status for document uploads"""
    return {
        "status": "operational",
        "supported_formats": ["pdf"],
        "max_file_size": "50MB",
        "available_models": list(llm_mgr.agents.keys())
    }