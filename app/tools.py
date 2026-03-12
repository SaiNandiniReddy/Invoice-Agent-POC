"""
app/tools.py — Mock tool implementations for the Invoice Workflow Agent POC.

Phase 4 additions:
  - PauseForApproval  : Custom exception implementing InterruptionRequirement.
                        request_approval raises this; main.py catches it,
                        collects human decision, then re-runs the agent.
  - get_approval_result: Helper called by request_approval on the second run
                         (after the human has already decided) to return the
                         stored decision without raising again.

Tools
─────
1. validate_vendor    — Is the vendor registered? Does the GSTIN match?
2. validate_po        — Does the PO exist? What is its approved amount?
3. check_duplicate    — Has this invoice already been processed?
4. request_approval   — HITL: raises PauseForApproval; resumed by main.py.
5. post_to_erp        — Mock ERP post; returns a UUID reference ID.
6. get_invoice_summary — Helper that reads the shared workflow store.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

import app.config as config


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Human-in-the-Loop: Custom Interruption Mechanism
# ─────────────────────────────────────────────────────────────────────────────

class PauseForApproval(Exception):
    """
    Custom exception that implements the InterruptionRequirement concept.

    Why a custom exception instead of agents.interrupt()?
    ─────────────────────────────────────────────────────
    The installed agents SDK version does not export interrupt().
    This custom exception achieves the same Human-in-the-Loop goal:

    1. request_approval raises PauseForApproval with invoice details.
    2. The Runner.run() call in main.py propagates the exception upward.
    3. main.py catches it, shows an approval panel to the human reviewer.
    4. The human types "approve" or "reject".
    5. main.py writes the decision to workflow_store.
    6. main.py re-invokes the agent with an updated message that includes
       the human decision — the agent reads it via get_invoice_summary()
       and continues the workflow from that point.

    Attributes
    ──────────
    invoice_id : str   — Which invoice needs approval.
    amount     : float — Invoice amount in INR.
    reason     : str   — Why the agent is requesting approval.
    """
    def __init__(self, invoice_id: str, amount: float, reason: str) -> None:
        self.invoice_id = invoice_id
        self.amount     = amount
        self.reason     = reason
        super().__init__(
            f"HITL approval required for invoice '{invoice_id}' "
            f"(INR {amount:,.2f}): {reason}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Shared In-Memory Workflow Store
# ─────────────────────────────────────────────────────────────────────────────
# Maps  invoice_id  →  dict with snapshot of the most recent call results.
# In production you would use a proper database / state machine.

workflow_store: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Private Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _update_store(invoice_id: str, data: dict) -> None:
    """
    Merge `data` into the workflow_store entry for `invoice_id`.
    Creates the entry if it does not yet exist.
    """
    if invoice_id not in workflow_store:
        workflow_store[invoice_id] = {}
    workflow_store[invoice_id].update(data)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — validate_vendor
# ─────────────────────────────────────────────────────────────────────────────

class ValidateVendorInput(BaseModel):
    invoice_id:   str
    vendor_name:  str
    vendor_gstin: str


class ValidateVendorOutput(BaseModel):
    is_valid: bool
    reason:   str


def validate_vendor(input: ValidateVendorInput) -> ValidateVendorOutput:
    """
    Check whether the vendor is registered in the system.

    Validation rules
    ─────────────────
    1. vendor_name must exist in config.VALID_VENDORS.
    2. The vendor_gstin supplied on the invoice must EXACTLY match the
       GSTIN on record for that vendor name.

    Returns
    ───────
    ValidateVendorOutput
        is_valid=True   if both checks pass.
        is_valid=False  + a descriptive reason if either check fails.
    """
    if input.vendor_name not in config.VALID_VENDORS:
        return ValidateVendorOutput(
            is_valid=False,
            reason=f"Vendor '{input.vendor_name}' is not registered in the system.",
        )

    expected_gstin = config.VALID_VENDORS[input.vendor_name]
    if input.vendor_gstin.upper() != expected_gstin.upper():
        return ValidateVendorOutput(
            is_valid=False,
            reason=(
                f"GSTIN mismatch for vendor '{input.vendor_name}'. "
                f"Invoice has '{input.vendor_gstin}', but system has '{expected_gstin}'."
            ),
        )

    _update_store(input.invoice_id, {"vendor_validated": True, "vendor_name": input.vendor_name})
    return ValidateVendorOutput(
        is_valid=True,
        reason=f"Vendor '{input.vendor_name}' is registered and GSTIN matches.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — validate_po
# ─────────────────────────────────────────────────────────────────────────────

class ValidatePOInput(BaseModel):
    invoice_id: str
    po_number:  str


class ValidatePOOutput(BaseModel):
    is_valid:  bool
    reason:    str
    po_amount: float | None = None


def validate_po(input: ValidatePOInput) -> ValidatePOOutput:
    """
    Check whether the PO number exists and is active.

    Returns
    ───────
    ValidatePOOutput
        is_valid=True  + po_amount  if the PO exists.
        is_valid=False + reason     if the PO does not exist.
    """
    if input.po_number not in config.VALID_POS:
        return ValidatePOOutput(
            is_valid=False,
            reason=f"PO number '{input.po_number}' does not exist in the system.",
            po_amount=None,
        )

    approved_amount = config.VALID_POS[input.po_number]
    _update_store(input.invoice_id, {
        "po_validated": True,
        "po_number": input.po_number,
        "po_amount": approved_amount,
    })
    return ValidatePOOutput(
        is_valid=True,
        reason=f"PO '{input.po_number}' is active. Approved amount: INR {approved_amount:,.2f}.",
        po_amount=approved_amount,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — check_duplicate
# ─────────────────────────────────────────────────────────────────────────────

class CheckDuplicateInput(BaseModel):
    invoice_id: str


class CheckDuplicateOutput(BaseModel):
    is_duplicate: bool
    reason:       str


def check_duplicate(input: CheckDuplicateInput) -> CheckDuplicateOutput:
    """
    Detect whether this invoice has already been processed.

    Uses config.PROCESSED_INVOICES (an in-memory set) as the registry.
    When an invoice is NOT a duplicate it is immediately registered so
    subsequent calls for the same invoice_id correctly return is_duplicate=True.

    Returns
    ───────
    CheckDuplicateOutput
        is_duplicate=True   if the invoice_id was already registered.
        is_duplicate=False  if this is the first time we see this invoice.
    """
    if input.invoice_id in config.PROCESSED_INVOICES:
        return CheckDuplicateOutput(
            is_duplicate=True,
            reason=f"Invoice '{input.invoice_id}' has already been processed. Rejecting as duplicate.",
        )

    config.PROCESSED_INVOICES.add(input.invoice_id)
    _update_store(input.invoice_id, {"duplicate_checked": True})
    return CheckDuplicateOutput(
        is_duplicate=False,
        reason=f"Invoice '{input.invoice_id}' is not a duplicate. Registered for processing.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — request_approval  (Phase 4: Human-in-the-Loop)
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalInput(BaseModel):
    invoice_id: str
    amount:     float
    reason:     str


class ApprovalOutput(BaseModel):
    approval_status: str           # "pending" | "approved" | "rejected"
    approver:        str | None = None
    notes:           str | None = None


def request_approval(input: ApprovalInput) -> ApprovalOutput:
    """
    Request human approval for a high-value invoice.

    Phase 4 behaviour — HUMAN-IN-THE-LOOP (InterruptionRequirement pattern)
    ─────────────────────────────────────────────────────────────────────────
    First call (before human decides):
      1. Saves "pending" state to workflow_store.
      2. Raises PauseForApproval — propagates out of Runner.run().
      3. main.py catches it, shows an approval panel.
      4. The human types "approve" or "reject".
      5. main.py writes the decision to workflow_store and re-runs the agent.

    Second call (after human decides — agent resumed with decision in message):
      The agent reads get_invoice_summary() first, sees approval_status is
      "approved" or "rejected", and returns accordingly without raising.

    Returns
    ───────
    ApprovalOutput with approval_status set to "approved" or "rejected".
    """
    # Check if a human decision has already been recorded (resume path)
    store = workflow_store.get(input.invoice_id, {})
    existing_status = store.get("approval_status", "")

    if existing_status in ("approved", "rejected"):
        # Resume path: human has already decided — return the stored result
        return get_approval_result(
            invoice_id=input.invoice_id,
            amount=input.amount,
            reason=input.reason,
        )

    # First call path: save pending state and raise to pause execution
    _update_store(
        input.invoice_id,
        {
            "approval_requested": True,
            "approval_status": "pending",
            "approval_reason": input.reason,
            "approval_amount": input.amount,
        },
    )

    # ── PAUSE HERE — raise the HITL exception ────────────────────────────────
    # main.py catches this, collects human input, updates workflow_store,
    # then re-runs the agent with the approval outcome in the message.
    raise PauseForApproval(
        invoice_id=input.invoice_id,
        amount=input.amount,
        reason=input.reason,
    )


def get_approval_result(invoice_id: str, amount: float, reason: str) -> ApprovalOutput:
    """
    Return the stored approval decision without raising PauseForApproval.

    Called by request_approval when workflow_store already has a non-pending
    approval_status (i.e., the human has already decided on the resume path).
    """
    store = workflow_store.get(invoice_id, {})
    decision = store.get("approval_status", "rejected")

    if decision == "approved":
        _update_store(invoice_id, {"approval_status": "approved"})
        return ApprovalOutput(
            approval_status="approved",
            approver="human-reviewer",
            notes=(
                f"Invoice '{invoice_id}' (INR {amount:,.2f}) "
                "was APPROVED by the human reviewer. Proceeding to ERP post."
            ),
        )
    else:
        _update_store(invoice_id, {"approval_status": "rejected"})
        return ApprovalOutput(
            approval_status="rejected",
            approver="human-reviewer",
            notes=(
                f"Invoice '{invoice_id}' (INR {amount:,.2f}) "
                f"was REJECTED by the human reviewer. Reason: {reason}."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — post_to_erp
# ─────────────────────────────────────────────────────────────────────────────

class ERPPostInput(BaseModel):
    invoice_id:   str
    vendor_gstin: str
    po_number:    str
    amount:       float


class ERPPostOutput(BaseModel):
    success:          bool
    erp_reference_id: str | None = None
    reason:           str


def post_to_erp(input: ERPPostInput) -> ERPPostOutput:
    """
    Post a validated invoice to the ERP system (mock).

    Mock behaviour
    ──────────────
    • Always succeeds for any non-empty inputs.
    • Generates a unique ERP reference ID using uuid4.
    • Format: ERP-<8 uppercase hex chars>  e.g. ERP-A3F9C120

    Guardrail (Phase 4)
    ────────────────────
    ERPPostGuardrail in guardrails.py blocks this tool from firing unless
    vendor_validated=True and po_validated=True are present in workflow_store.

    Returns
    ───────
    ERPPostOutput
        success=True  + erp_reference_id  on success.
        success=False + reason            if required fields are missing.
    """
    if not all([input.invoice_id, input.vendor_gstin, input.po_number]):
        return ERPPostOutput(
            success=False,
            erp_reference_id=None,
            reason="ERP post failed: one or more required fields are empty.",
        )

    reference_id = "ERP-" + uuid.uuid4().hex[:8].upper()
    timestamp = datetime.utcnow().isoformat()

    _update_store(
        input.invoice_id,
        {
            "erp_posted": True,
            "erp_reference_id": reference_id,
            "erp_posted_at": timestamp,
        },
    )

    return ERPPostOutput(
        success=True,
        erp_reference_id=reference_id,
        reason=(
            f"Invoice '{input.invoice_id}' successfully posted to ERP. "
            f"Reference ID: {reference_id}. Posted at: {timestamp} UTC."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6 (Helper) — get_invoice_summary
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowSummaryInput(BaseModel):
    invoice_id: str


class WorkflowSummaryOutput(BaseModel):
    invoice_id:      str
    status:          str
    tools_called:    list[str]
    approval_status: str


def get_invoice_summary(input: WorkflowSummaryInput) -> WorkflowSummaryOutput:
    """
    Return a snapshot summary of an invoice's current processing state.

    The agent calls this to get a bird's-eye view of what has happened
    so far before deciding its next action.

    Returns
    ───────
    WorkflowSummaryOutput
        invoice_id       : echoed back for clarity
        status           : current pipeline status string
        tools_called     : list of tool names executed so far
        approval_status  : "not_required" | "pending" | "approved" | "rejected"
    """
    store = workflow_store.get(input.invoice_id, {})

    tools_called: list[str] = []
    if store.get("vendor_validated"):
        tools_called.append("validate_vendor")
    if store.get("po_validated"):
        tools_called.append("validate_po")
    if store.get("duplicate_checked"):
        tools_called.append("check_duplicate")
    if store.get("approval_requested"):
        tools_called.append("request_approval")
    if store.get("erp_posted"):
        tools_called.append("post_to_erp")

    if store.get("erp_posted"):
        status = "completed"
    elif store.get("approval_requested"):
        status = "awaiting_approval"
    elif tools_called:
        status = "in_progress"
    else:
        status = "pending"

    approval_status = store.get("approval_status", "not_required")

    return WorkflowSummaryOutput(
        invoice_id=input.invoice_id,
        status=status,
        tools_called=tools_called,
        approval_status=approval_status,
    )
