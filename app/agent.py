"""
app/agent.py — Agent configuration and tool wiring for Phase 3.

Phase 3 adds:
  - SDK-compatible tool wrapper functions (flat str/float params)
  - invoice_agent: fully configured Agent with 6 tools + structured output

WHY WRAPPER FUNCTIONS?
  The OpenAI Agents SDK inspects Python function signatures to build the
  JSON schema it sends to the LLM. Each parameter must be a simple type
  (str, float) — not a nested Pydantic model.

  The wrappers:
    1. Accept flat parameters (str / float)
    2. Build the Pydantic input models internally
    3. Call the original tools from tools.py
    4. Return a plain string the LLM can read

  This keeps tools.py (unit-tested with Pydantic) unchanged.
"""

from __future__ import annotations

from agents import Agent
import app.config as config
from app.guardrails import erp_post_guardrail  # Phase 4: ERP post safety guardrail
from app.state import NextActionDecision
from app.tools import (
    ApprovalInput,
    CheckDuplicateInput,
    ERPPostInput,
    ValidatePOInput,
    ValidateVendorInput,
    WorkflowSummaryInput,
    check_duplicate,
    get_invoice_summary,
    post_to_erp,
    request_approval,
    validate_po,
    validate_vendor,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent System Instructions
# ─────────────────────────────────────────────────────────────────────────────

INVOICE_AGENT_INSTRUCTIONS = """
You are an invoice processing agent. Your job is to validate invoices
and orchestrate the approval workflow.

Follow this decision sequence for EVERY invoice:

1. ALWAYS call tool_validate_vendor first.
   - If vendor is invalid → return next_action="rejected" immediately.

2. Call tool_validate_po next.
   - If PO is invalid → return next_action="rejected" immediately.

3. Call tool_check_duplicate.
   - If duplicate detected → return next_action="rejected" immediately.

4. Check invoice_amount:
   - If amount > 100000 INR → call tool_request_approval.
     Then return next_action="request_approval".
   - If amount <= 100000 INR → skip to step 5.

5. Call tool_post_to_erp.
   - If ERP post succeeds → return next_action="complete".
   - If ERP post fails   → return next_action="manual_review".

Return a structured NextActionDecision with:
  - next_action    : final action (complete | rejected | request_approval | manual_review)
  - reason         : clear English explanation of WHY you chose this action
  - confidence     : float between 0.0 and 1.0 representing your certainty
  - required_input : any extra data needed (or null)

Never skip steps. Always reason from tool outputs.
"""


# ─────────────────────────────────────────────────────────────────────────────
# SDK-Compatible Tool Wrappers
# ─────────────────────────────────────────────────────────────────────────────

def tool_validate_vendor(invoice_id: str, vendor_name: str, vendor_gstin: str) -> str:
    """
    Check if the vendor is registered and their GSTIN matches our records.
    MUST be called first for every invoice.
    Returns is_valid (True/False) and a descriptive reason.
    """
    result = validate_vendor(
        ValidateVendorInput(
            invoice_id=invoice_id,
            vendor_name=vendor_name,
            vendor_gstin=vendor_gstin,
        )
    )
    return f"is_valid={result.is_valid} | reason={result.reason}"


def tool_validate_po(invoice_id: str, po_number: str) -> str:
    """
    Check if the Purchase Order exists and return its approved budget in INR.
    Call this after tool_validate_vendor passes.
    Returns is_valid (True/False), po_amount, and reason.
    """
    result = validate_po(ValidatePOInput(invoice_id=invoice_id, po_number=po_number))
    return f"is_valid={result.is_valid} | po_amount={result.po_amount} | reason={result.reason}"


def tool_check_duplicate(invoice_id: str) -> str:
    """
    Check if this invoice has already been processed before.
    Call this after tool_validate_po passes.
    Returns is_duplicate (True/False) and reason.
    """
    result = check_duplicate(CheckDuplicateInput(invoice_id=invoice_id))
    return f"is_duplicate={result.is_duplicate} | reason={result.reason}"


def tool_request_approval(invoice_id: str, amount: float, reason: str) -> str:
    """
    Request human approval for a high-value invoice (amount > INR 1,00,000).
    Only call when the invoice_amount exceeds the approval threshold.
    Returns approval_status (pending in Phase 3), approver, and notes.
    """
    result = request_approval(
        ApprovalInput(invoice_id=invoice_id, amount=amount, reason=reason)
    )
    return (
        f"approval_status={result.approval_status} | "
        f"approver={result.approver} | "
        f"notes={result.notes}"
    )


def tool_post_to_erp(
    invoice_id: str, vendor_gstin: str, po_number: str, amount: float
) -> str:
    """
    Post the validated invoice to the ERP system.
    Only call AFTER vendor, PO, and duplicate checks have all passed.
    Returns success (True/False) and an ERP reference ID on success.
    """
    result = post_to_erp(
        ERPPostInput(
            invoice_id=invoice_id,
            vendor_gstin=vendor_gstin,
            po_number=po_number,
            amount=amount,
        )
    )
    return (
        f"success={result.success} | "
        f"erp_reference_id={result.erp_reference_id} | "
        f"reason={result.reason}"
    )


def tool_get_invoice_summary(invoice_id: str) -> str:
    """
    Get a snapshot of the current processing state for an invoice.
    Use this to check which steps have been completed so far.
    Returns status, list of tools_called, and approval_status.
    """
    result = get_invoice_summary(WorkflowSummaryInput(invoice_id=invoice_id))
    return (
        f"status={result.status} | "
        f"tools_called={result.tools_called} | "
        f"approval_status={result.approval_status}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent Configuration
# ─────────────────────────────────────────────────────────────────────────────

invoice_agent = Agent(
    name="invoice-workflow-agent",
    instructions=INVOICE_AGENT_INSTRUCTIONS,
    tools=[
        tool_validate_vendor,
        tool_validate_po,
        tool_check_duplicate,
        tool_request_approval,
        tool_post_to_erp,
        tool_get_invoice_summary,
    ],
    output_type=NextActionDecision,
    model=config.OPENAI_MODEL,
    input_guardrails=[erp_post_guardrail],  # Phase 4: ERP post safety check
)
