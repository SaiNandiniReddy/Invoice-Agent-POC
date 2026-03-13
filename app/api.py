"""
app/api.py — FastAPI REST endpoint for the Invoice Workflow Agent POC.

Phase 5 Bonus: Exposes the invoice processing workflow as an HTTP API
so any system can submit an invoice via JSON POST instead of the CLI.

Endpoints
─────────
POST /process-invoice   — submit an invoice, get back the workflow result
GET  /health            — liveness check (no OpenAI call)
GET  /trace/{invoice_id} — retrieve the trace JSON for a processed invoice

How to run
──────────
    cd Invoice-Agent-POC
    uvicorn app.api:api --reload --port 8000

Then test with curl:
    curl -X POST http://localhost:8000/process-invoice \\
         -H "Content-Type: application/json" \\
         -d @sample_data/invoice_happy.json
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import app.config as config
from app.state import Invoice, WorkflowState, WorkflowStatus


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app instance
# ─────────────────────────────────────────────────────────────────────────────

api = FastAPI(
    title="Invoice Workflow Agent API",
    description=(
        "REST interface for the Invoice Workflow Agent POC. "
        "Processes invoices through validation, approval (HITL), and ERP posting."
    ),
    version="1.0.0",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allow any localhost origin so the HTML frontend can call the API during dev.
# In production, replace ["*"] with your real frontend domain.
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class InvoiceRequest(BaseModel):
    """
    Input model for POST /process-invoice.

    All fields mirror the Invoice schema in app/state.py.
    The API accepts JSON, validates it via Pydantic, and passes it to
    the same workflow agent used by the CLI.
    """
    invoice_id:     str
    vendor_name:    str
    vendor_gstin:   str
    po_number:      str
    invoice_amount: float
    invoice_date:   str
    currency:       str = "INR"
    line_items:     list[dict] = []


class WorkflowResponse(BaseModel):
    """
    Response model for POST /process-invoice.

    Returns the workflow outcome and where the trace was saved.
    """
    invoice_id:        str
    status:            str
    next_action:       str
    confidence:        float
    reason:            str
    erp_reference_id:  str | None = None
    rejection_reason:  str | None = None
    trace_path:        str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@api.get("/health")
def health_check() -> dict:
    """
    Liveness check — returns 200 OK if the service is running.

    Does NOT call OpenAI. Use this to verify the server started correctly.
    """
    return {
        "status": "ok",
        "service": "Invoice Workflow Agent API",
        "tracing_enabled": config.ENABLE_TRACING,
        "model": config.OPENAI_MODEL,
    }


@api.post("/process-invoice", response_model=WorkflowResponse)
async def process_invoice(request: InvoiceRequest) -> WorkflowResponse:
    """
    Process a single invoice through the full workflow agent.

    Steps performed:
      1. Validates the incoming JSON via Pydantic
      2. Runs the invoice agent (same as CLI, with tracing)
      3. Saves result JSON + trace JSON to output/
      4. Returns a structured workflow response

    NOTE: This endpoint is SYNCHRONOUS from the API caller's perspective
    — it waits until the agent completes. For very long-running invoices
    (e.g., those requiring human approval) this may time out.
    In production you would use a task queue (Celery / background tasks).

    Human-in-the-Loop limitation:
    When running via API, the approval step cannot pause for human input.
    Invoices requiring approval will return status="awaiting_approval".
    Use the CLI for interactive approval workflows.
    """
    if not config.OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured on the server.",
        )

    # Build the Invoice model from the request
    from app.state import LineItem
    line_items = [LineItem(**li) for li in request.line_items] if request.line_items else []

    invoice = Invoice(
        invoice_id=request.invoice_id,
        vendor_name=request.vendor_name,
        vendor_gstin=request.vendor_gstin,
        po_number=request.po_number,
        invoice_amount=request.invoice_amount,
        invoice_date=request.invoice_date,
        currency=request.currency,
        line_items=line_items,
    )

    # Run the agent
    from app.agent import invoice_agent
    from app.main import build_invoice_message, apply_decision_to_state, save_result
    from app.tracing import TraceCollector, sdk_trace
    from app.tools import workflow_store, PauseForApproval

    collector = TraceCollector(
        invoice_id=invoice.invoice_id,
        scenario=_infer_scenario(invoice.invoice_amount),
    )

    state = WorkflowState(invoice_id=invoice.invoice_id, status=WorkflowStatus.IN_PROGRESS)
    message = build_invoice_message(invoice)

    try:
        from agents import Runner
        with sdk_trace("InvoiceWorkflowAPI", collector.trace_id):
            result = await Runner.run(invoice_agent, message)
        decision = result.final_output

    except PauseForApproval as exc:
        # API cannot pause for human input — return awaiting_approval status
        collector.record(
            tool_name="request_approval",
            input_data={"invoice_id": exc.invoice_id, "amount": exc.amount},
            output_data={"approval_status": "awaiting_approval"},
            duration_ms=0,
            success=True,
        )
        trace_path = str(collector.save())
        return WorkflowResponse(
            invoice_id=invoice.invoice_id,
            status="awaiting_approval",
            next_action="request_approval",
            confidence=0.9,
            reason=exc.reason,
            trace_path=trace_path,
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    # Apply decision and save state
    apply_decision_to_state(state, decision, invoice)
    result_path = save_result(state)
    trace_path  = str(collector.save())

    return WorkflowResponse(
        invoice_id=invoice.invoice_id,
        status=state.status.value,
        next_action=decision.next_action,
        confidence=decision.confidence,
        reason=decision.reason,
        erp_reference_id=state.erp_reference_id,
        rejection_reason=state.rejection_reason,
        trace_path=trace_path,
    )


@api.get("/trace/{invoice_id}")
def get_trace(invoice_id: str) -> JSONResponse:
    """
    Retrieve the saved trace JSON for a previously processed invoice.

    Returns 404 if no trace exists yet for this invoice_id.
    """
    trace_file = Path(config.OUTPUT_DIR) / f"{invoice_id}_trace.json"
    if not trace_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No trace found for invoice_id='{invoice_id}'. "
                   "Has this invoice been processed?",
        )
    data = json.loads(trace_file.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


@api.get("/result/{invoice_id}")
def get_result(invoice_id: str) -> JSONResponse:
    """
    Retrieve the saved workflow result JSON for a processed invoice.
    """
    result_file = Path(config.OUTPUT_DIR) / f"{invoice_id}_result.json"
    if not result_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No result found for invoice_id='{invoice_id}'.",
        )
    data = json.loads(result_file.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


@api.get("/invoices/samples")
def get_sample_invoices() -> JSONResponse:
    """
    Return the 3 built-in sample invoice payloads so the frontend
    can populate form fields without the user typing anything.
    """
    samples_dir = Path(__file__).parent.parent / "sample_data"
    samples = []
    for fname in ("invoice_happy.json", "invoice_approval.json", "invoice_failure.json"):
        fpath = samples_dir / fname
        if fpath.exists():
            samples.append(json.loads(fpath.read_text(encoding="utf-8")))
    return JSONResponse(content=samples)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _infer_scenario(amount: float) -> str:
    """Guess the scenario name based on invoice amount."""
    if amount >= config.APPROVAL_THRESHOLD:
        return "approval_path"
    return "happy_path"
