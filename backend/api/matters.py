"""Matters — the durable object a review lives in.

A review used to live in one browser tab: the contract list was in
``localStorage``, the selected contract was React state with no URL, and the
only list endpoint was ``/api/documents/debug/contracts``, gated on
``VIEW_AUDIT`` — a permission the default ``LEGAL_REVIEWER`` role does not hold.
A colleague, a second machine or a hard refresh all saw nothing.

These three reads and one write are what the app navigates by.

Tenancy comes from ``Depends(get_current_tenant)`` throughout, never from a
query parameter. An unknown reference and another tenant's reference both
answer **404**, the same rule redlines follow: 403 would confirm that the
reference exists, which is the disclosure the status code was meant to prevent.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.domain.matter import (
    InvalidTransition,
    MatterStatus,
    check_transition,
    derive_status,
    is_reference,
    parse_status,
)
from backend.governance.rbac import Permission, get_current_tenant, requires_permission
from backend.infrastructure.matter_repository import (
    AlreadyFiled,
    MatterRepository,
)
from backend.shared.debug import note, trace_step
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/matters", tags=["matters"])

repository = MatterRepository()


def _summary_row(row: dict, review: Optional[dict] = None) -> dict:
    """One matter as the list renders it, with its status derived."""
    latest_n = row.get("latest_n")
    counts = review or {
        "total": int(row.get("redline_total") or 0),
        "pending": int(row.get("redline_pending") or 0),
    }
    return {
        "matter_ref": row.get("matter_ref"),
        "title": row.get("title") or "",
        "counterparty": row.get("counterparty") or "",
        "contract_type": row.get("contract_type") or "",
        "status": derive_status(row.get("status"), counts).value,
        "stored_status": parse_status(row.get("status")).value,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "version_count": int(row.get("version_count") or 0),
        "latest_version": int(latest_n) if latest_n is not None else None,
        "latest_version_id": row.get("latest_version_id"),
        "analysis_status": row.get("analysis_status") or "NOT_STARTED",
        "risk_score": row.get("risk_score"),
        "risk_level": row.get("risk_level"),
        "redlines_total": counts.get("total", 0),
        "redlines_pending": counts.get("pending", 0),
    }


@router.get("", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def list_matters(
    tenant_id: str = Depends(get_current_tenant),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """This tenant's matters — the app's landing page.

    Paged, and **it says so**. A landing page that silently returns the first N
    makes everything older unreachable, which for a system whose whole claim is
    that the review is durable would be the worst possible failure: the work is
    still there, and there is no way to get to it. `total` and `has_more` are
    part of the answer so the page can ask for the rest and the reviewer can see
    that there is a rest.
    """
    total = repository.count_matters(tenant_id)
    rows = repository.list_matters(tenant_id, limit=limit, offset=offset)
    matters = [_summary_row(row) for row in rows]
    return {
        "count": len(matters),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(matters) < total,
        "matters": matters,
    }


@router.get("/{matter_ref}", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_matter(matter_ref: str, tenant_id: str = Depends(get_current_tenant)):
    """One matter and every round of it.

    A matter with no versions cannot exist — one is created only after an
    extraction succeeds — so an empty ``versions`` list here means the data was
    edited by hand, not that the reviewer has nothing to look at.
    """
    matter = repository.get_matter(tenant_id, matter_ref)
    if matter is None:
        raise HTTPException(status_code=404, detail=f"No matter {matter_ref}")

    counts = repository.review_counts(tenant_id, matter_ref)
    versions = sorted(
        (v for v in (matter.get("versions") or []) if v.get("version_id")),
        key=lambda v: v.get("n") or 0,
    )
    latest_n = versions[-1]["n"] if versions else None

    return {
        "matter_ref": matter["matter_ref"],
        "title": matter.get("title") or "",
        "counterparty": matter.get("counterparty") or "",
        "contract_type": matter.get("contract_type") or "",
        "status": derive_status(matter.get("status"), counts.get(latest_n, {})).value,
        "stored_status": parse_status(matter.get("status")).value,
        "created_at": matter.get("created_at"),
        "updated_at": matter.get("updated_at"),
        "latest_version": latest_n,
        "versions": [
            {
                "n": v.get("n"),
                "version_id": v.get("version_id"),
                "filename": v.get("filename") or "",
                "uploaded_at": v.get("uploaded_at"),
                "source_sha256": v.get("source_sha256"),
                "analysis_status": v.get("analysis_status") or "NOT_STARTED",
                # Never rendered as "no findings": the matter page shows this as
                # a warning above the (empty) results.
                "analysis_error": v.get("analysis_error") or "",
                "analysis_updated_at": v.get("analysis_updated_at"),
                "risk_score": v.get("risk_score"),
                "risk_level": v.get("risk_level"),
                "clauses_count": v.get("clauses_count"),
                "violations_count": v.get("violations_count"),
                "review": counts.get(v.get("n"), {
                    "total": 0, "pending": 0, "approved": 0, "modified": 0, "rejected": 0,
                }),
            }
            for v in versions
        ],
    }


class CreateMatterRequest(BaseModel):
    """The confirmation card, as the user corrected it.

    Everything but ``contract_id`` is pre-filled from what extraction already
    found, so the reviewer edits rather than types — and the reference number is
    allocated only when they confirm, so cancelling burns nothing.
    """

    contract_id: str = Field(description="the version returned by the upload")
    title: str = Field(default="", description="defaults to type — counterparty")
    counterparty: str = Field(default="")
    contract_type: str = Field(default="", description="decides the reference prefix")


@router.post("", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
async def create_matter(
    request: CreateMatterRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """File an uploaded document as version 1 of a new matter.

    The user always chooses. There is no similarity matching here on purpose: a
    new SOW for a different vendor off the same template looks exactly like a
    new round of the old one, and only the user knows which it is. The single
    automatic case — byte-identical source — is handled at upload, before this.
    """
    title = request.title.strip()
    if not title:
        from backend.domain.matter import suggest_title

        title = suggest_title(request.contract_type, request.counterparty, request.contract_id)

    try:
        with trace_step("matter", "create", contract_id=request.contract_id) as step:
            created = repository.create_matter(
                tenant_id,
                request.contract_id,
                title=title,
                counterparty=request.counterparty.strip(),
                contract_type=request.contract_type.strip(),
            )
            step.set(matter_ref=created["matter_ref"])
    except AlreadyFiled:
        existing = repository.matter_for_version(tenant_id, request.contract_id)
        if existing:
            # A double-clicked confirm lands here. Report the matter it already
            # made rather than allocating a second reference for the same bytes.
            raise HTTPException(
                status_code=409,
                detail=f"{request.contract_id} is already version {existing['n']} "
                       f"of {existing['matter_ref']}",
            )
        raise HTTPException(
            status_code=404,
            detail=f"No uploaded document {request.contract_id} to file",
        )

    note("matter", "created", matter_ref=created["matter_ref"],
         contract_id=request.contract_id)
    return {
        "matter_ref": created["matter_ref"],
        "version": created["n"],
        "version_id": request.contract_id,
        "title": title,
        "status": MatterStatus.DRAFT.value,
    }


class StatusChangeRequest(BaseModel):
    status: MatterStatus = Field(
        description="the status to move to. IN_REVIEW and REVIEWED are derived "
                    "from the redline decisions and cannot be set directly"
    )


@router.patch("/{matter_ref}/status",
              dependencies=[Depends(requires_permission(Permission.APPROVE_REDLINE))])
async def change_status(
    matter_ref: str,
    request: StatusChangeRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Move a matter along — including reopening a closed one.

    Guarded by APPROVE_REDLINE rather than ANALYZE for the reason the redline
    decision endpoint is: VIEWER holds ANALYZE, and closing a matter is a
    judgement about the negotiation, not a query against it.
    """
    if not is_reference(matter_ref):
        raise HTTPException(status_code=404, detail=f"No matter {matter_ref}")

    matter = repository.get_matter(tenant_id, matter_ref)
    if matter is None:
        raise HTTPException(status_code=404, detail=f"No matter {matter_ref}")

    counts = repository.review_counts(tenant_id, matter_ref)
    latest_n = max(counts) if counts else None
    current = derive_status(matter.get("status"), counts.get(latest_n, {}))

    try:
        target = check_transition(current, request.status)
    except InvalidTransition as e:
        raise HTTPException(status_code=422, detail=str(e))

    repository.set_status(tenant_id, matter_ref, target)
    logger.info(f"Matter {matter_ref}: {current.value} -> {target.value}")
    note("matter", "status_changed", matter_ref=matter_ref,
         previous=current.value, status=target.value)
    return {
        "matter_ref": matter_ref,
        "status": target.value,
        "previous_status": current.value,
    }
