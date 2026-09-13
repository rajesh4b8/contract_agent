import json
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, AIMessageChunk
from backend.llm_manager import LLMManager
from backend.api.document_upload import router as document_router
from backend.api.contract_intelligence import router as intelligence_router
from backend.api.routes.debug import create_debug_router
from backend.api.routes.production import create_production_router
from backend.shared.utils.route_utils import is_development, conditionally_include_router
from backend.api.enhanced_contract_search import router as enhanced_search_router
from backend.api.enhanced_document_upload import router as enhanced_upload_router
from backend.agents.agent_workflow_tracker import get_current_workflow_status
from backend.shared.middleware.tracing import TracingMiddleware
from backend.shared.utils.logger import get_logger, correlation_id_var
from backend.governance.prompt_guard import PromptGuard
from backend.governance.output_guard import OutputGuard
from backend.governance.rbac import Permission, requires_permission
from backend.infrastructure.audit_logger import AuditLogger
from backend.shared.errors import LLMProviderError, describe_llm_error
from backend.shared.debug import note, trace_step

logger = get_logger(__name__)

import os
from openinference.instrumentation.langchain import LangChainInstrumentor

load_dotenv()

# Initialize Phoenix tracing (OpenTelemetry)
phoenix_endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
try:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=phoenix_endpoint)))
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
except Exception as e:
    logger.warning(f"Failed to initialize OpenTelemetry tracing: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Initialize once
    app.state.llm_manager = LLMManager()
    yield
    # Shutdown - cleanup if needed

app = FastAPI(lifespan=lifespan)


@app.exception_handler(LLMProviderError)
async def llm_provider_error_handler(request: Request, exc: LLMProviderError):
    """Answer a classified model failure with its own status and explanation.

    Without this every one of them arrived as a 500 "Processing failed:
    <provider stack trace>", which tells a reviewer nothing about the fact
    that the day's free quota is simply gone.
    """
    info = exc.info
    logger.error(
        f"LLM provider failure ({info.failure.value}) on {request.url.path}: {info.detail}"
    )
    headers = {"Retry-After": str(info.retry_after)} if info.retry_after else None
    return JSONResponse(status_code=info.status_code, content=info.to_dict(), headers=headers)


# Dependency injection
def get_llm_manager(request: Request):
    return request.app.state.llm_manager


app.add_middleware(TracingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],  # Allow all origins
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Include routers based on environment
from backend.api.models_api import router as models_router
app.include_router(models_router)
app.include_router(document_router)
app.include_router(intelligence_router)
app.include_router(enhanced_search_router, prefix="/api")
app.include_router(enhanced_upload_router)

# Supervisor API
from backend.api.supervisor_api import router as supervisor_router
app.include_router(supervisor_router)

# Feedback API (Phase 2)
from backend.api.feedback_api import router as feedback_router
app.include_router(feedback_router)

# Monitoring API (Phase 3)
from backend.api.monitoring_api import router as monitoring_router
app.include_router(monitoring_router)

# Audit API (Production)
from backend.api.audit_api import router as audit_router
app.include_router(audit_router)

# AI Patterns API
from backend.api.patterns_api import router as patterns_router
app.include_router(patterns_router)

# Policy Management API
from backend.api.policy_api import router as policy_router
app.include_router(policy_router)

# Debug routes (development only)
debug_router = create_debug_router()
conditionally_include_router(app, debug_router, is_development())

# Developer debug event stream. Mounted in development only, and the event
# endpoints additionally require DEBUG_EVENTS — they carry filenames, tenant ids
# and contract ids to an unauthenticated caller, so the environment gate is
# enforced in three places rather than promised in the docs: here, in the
# endpoints, and in `debug_events_enabled()`, which also stops a misconfigured
# production process from buffering anything at all.
from backend.api.debug_events import router as debug_events_router
conditionally_include_router(app, debug_events_router, is_development())

@app.get("/api/workflow/status", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_workflow_status():
    """Get current multi-agent workflow status for executive dashboard"""
    return get_current_workflow_status()

@app.get("/api/planning/status", dependencies=[Depends(requires_permission(Permission.VIEW_REPORTS))])
async def get_planning_status():
    """Get autonomous planning agent status"""
    from backend.agents.planning.planning_agent import PlanningAgentFactory
    
    planning_agent = PlanningAgentFactory.create_planning_agent()
    return {
        "agent_type": "Autonomous Planning & Reasoning Agent",
        "capabilities": [
            "Query Analysis & Decomposition",
            "Execution Plan Generation", 
            "Self-Reflection & Validation",
            "Adaptive Strategy Selection",
            "Performance Learning"
        ],
        "available_strategies": ["simple", "complex", "risk_focused", "compliance_focused"],
        "execution_history_count": len(planning_agent.execution_history)
    }


@app.get("/")
async def root():
    return {"status": "OK"}


class RunPayload(BaseModel):
    model: str
    prompt: str
    history: str

def rebuild_history(history):
    history = json.loads(history)

    type_to_class = {
        "human": HumanMessage,
        "tool": ToolMessage,
        "ai": AIMessage
    }

    messages = []
    for item_json_str in history:
        item = json.loads(item_json_str)
        item_class = type_to_class.get(item["type"])
        if item_class:
            # use pydantic BaseClass method to rebuild message model from json string dumped by model_dump_json
            messages.append(item_class.model_validate_json(item_json_str))

    return messages


async def runner(model: str, prompt: str, history: str, llm_mgr: LLMManager, user_role: str = "unknown"):
    """Stream a chat turn, reporting a model failure instead of dying silently.

    An exception raised inside a streaming generator just closes the SSE
    connection: the chat is left with a half-written answer and "Failed to
    generate the response", whatever actually went wrong. Anything the
    provider refuses — quota gone, key rejected, safety filter — is sent as an
    `error` part the UI renders in place.
    """
    try:
        async for event in _run_chat_turn(model, prompt, history, llm_mgr, user_role):
            yield event
    except Exception as exc:  # noqa: BLE001 - the stream is the only channel back
        message = describe_llm_error(
            exc, model,
            fallback=f"The assistant could not complete this request: {exc}",
        )
        logger.error(f"Chat turn failed for model '{model}': {exc}", exc_info=True)
        yield f"data: {json.dumps({'content': message, 'type': 'error'})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"


async def _run_chat_turn(model: str, prompt: str, history: str, llm_mgr: LLMManager, user_role: str = "unknown"):
    logger.info(f"Processing LLM request for model '{model}' for user_role '{user_role}'")
    
    # Initialize AuditLogger and AgentAuditService for Guard persistence
    from backend.infrastructure.agent_audit_service import AgentAuditService
    
    audit_logger = AuditLogger()
    agent_audit = AgentAuditService(audit_logger)
    session_id = correlation_id_var.get() or "unknown_session"
    context_metadata = {"user_role": user_role}
    
    # 0. Log User Interaction
    agent_audit.log_user_interaction(user_id="user", prompt=prompt, session_id=session_id)

    note("chat", "turn_started", model=model, prompt_chars=len(prompt), role=user_role)

    # 1. Prompt Guard Pre-Check
    guard = PromptGuard(audit_logger=audit_logger)
    with trace_step("chat", "prompt_guard") as step:
        guard_result = guard.validate(prompt, context_metadata=context_metadata)
        step.set(safe=guard_result.is_safe, violation=guard_result.violation_type)
    
    # Log Prompt Guard Check
    agent_audit.log_guard_check(
        guard_name="Prompt Guard",
        is_safe=guard_result.is_safe,
        violation_type=guard_result.violation_type,
        session_id=session_id
    )

    if not guard_result.is_safe:
        logger.error(f"Prompt blocked by Guard: {guard_result.violation_type}")
        note("chat", "blocked", reason=str(guard_result.violation_type))
        yield f"data: {json.dumps({'content': guard_result.message, 'type': 'error'})}\n\n"
        yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"
        return

    # history comes in from FE as stringified list of dumped model messages
    if history != "[]":
        previous_messages = rebuild_history(history)
    else:
        previous_messages = []

    prompt_message = HumanMessage(content=prompt)
    input_messages = [*previous_messages, prompt_message]
    
    corr_id = correlation_id_var.get()
    run_tags = [f"correlation_id:{corr_id}"] if corr_id else []
    
    messages = llm_mgr.get_model_by_name(model).astream(
        input={"messages": input_messages}, 
        config={"tags": run_tags},
        stream_mode=["messages", "updates"]
    )

    # Context management
    context = json.loads(history)
    context.append(prompt_message.model_dump_json())
    
    # Buffer for post-check
    ai_full_content = ""
    # Time to first token is the number that decides whether the chat feels
    # broken: everything after it streams, everything before it is a blank box.
    first_token_seen = False
    note("chat", "stream_started", model=model)

    async for message in messages:
        if message[0] == "messages":
            chunk = message[1]

            # output tool call section type
            if hasattr(chunk[0], "tool_calls") and len(chunk[0].tool_calls) > 0:
                for tool in chunk[0].tool_calls:
                    if tool.get('name'):
                        tool_calls_content = json.dumps(tool)
                        yield f"data: {json.dumps({'content': tool_calls_content, 'type': 'tool_call'})}\n\n"

            if isinstance(chunk[0], ToolMessage):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'tool_message'})}\n\n"
            if isinstance(chunk[0], AIMessageChunk):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'ai_message'})}\n\n"
            if isinstance(chunk[0], HumanMessage):
                yield f"data: {json.dumps({'content': chunk[0].content, 'type': 'user_message'})}\n\n"

        if message[0] == "updates":
            # use pydantic BaseClass method model_dump_json to dump message model to be stringified into history
            if "assistant" in message[1]:
                for history_message in message[1]["assistant"]["messages"]:
                    context.append(history_message.model_dump_json())
            elif "tools" in message[1]:
                for tool_message in message[1]["tools"]["messages"]:
                    if hasattr(tool_message, 'model_dump_json'):
                        context.append(tool_message.model_dump_json())
                    else:
                        context.append(json.dumps(tool_message))
        
        # Capture AI content for post-check
        if message[0] == "messages":
            chunk = message[1][0]
            if isinstance(chunk, AIMessageChunk):
                # Only a real token counts. The stream also carries `updates`
                # frames and tool-call chunks, and marking the first of those
                # would report a time-to-first-token that had not happened yet.
                if not first_token_seen and chunk.content:
                    first_token_seen = True
                    note("chat", "first_token", model=model)
                ai_full_content += chunk.content

    # 2. Llama Guard Post-Check
    # Extract source context from tool results for hallucination check
    tool_contents = []
    for msg_str in context:
        try:
            msg = json.loads(msg_str)
            if msg.get("type") == "tool":
                tool_contents.append(msg.get("content", ""))
        except Exception:
            pass
    
    if tool_contents:
        context_metadata["source_text"] = "\n---\n".join(tool_contents)

    note("chat", "stream_ended", response_chars=len(ai_full_content))

    output_guard = OutputGuard(audit_logger=audit_logger)
    with trace_step("chat", "output_guard") as step:
        post_check_result = output_guard.validate(ai_full_content, context_metadata=context_metadata)
        step.set(safe=post_check_result.is_safe, violation=post_check_result.violation_type)
    
    # Log Output Guard Check
    agent_audit.log_guard_check(
        guard_name="Output Guard",
        is_safe=post_check_result.is_safe,
        violation_type=post_check_result.violation_type,
        session_id=session_id
    )

    if not post_check_result.is_safe:
        logger.error(f"Output blocked by Llama Guard: {post_check_result.violation_type}")
        # Notify user about the violation even if part of the content was streamed
        yield f"data: {json.dumps({'content': ' [CONTENT REMOVED DUE TO SAFETY POLICY] ' + post_check_result.message, 'type': 'error'})}\n\n"
    else:
        # If PII was redacted, we should update the context
        redacted_content = post_check_result.metadata.get("redacted_content")
        if redacted_content and redacted_content != ai_full_content:
            logger.info("PII redaction applied to AI output")
            # Log PII redaction to audit trail
            audit_logger.log_event(
                event_type=AuditEventType.SECURITY_VIOLATION,
                resource_id=session_id,
                action="pii_redaction",
                metadata={"status": "redacted"}
            )
            # Update the last message in context with the redacted version
            for i in range(len(context) - 1, -1, -1):
                msg_data = json.loads(context[i])
                if msg_data.get("type") == "ai":
                    msg_data["content"] = redacted_content
                    context[i] = json.dumps(msg_data)
                    break

    yield f"data: {json.dumps({'content': context, 'type': 'history'})}\n\n"
    yield f"data: {json.dumps({'content': '', 'type': 'end'})}\n\n"


@app.post("/api/run/", dependencies=[Depends(requires_permission(Permission.ANALYZE))])
async def run(payload: RunPayload, llm_mgr: LLMManager = Depends(get_llm_manager)):
    return StreamingResponse(
        runner(model=payload.model, prompt=payload.prompt, history=payload.history, llm_mgr=llm_mgr),
        media_type="text/event-stream",
    )