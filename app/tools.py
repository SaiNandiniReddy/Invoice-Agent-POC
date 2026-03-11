"""
app/tools.py — Mock tool implementations for the Invoice Workflow Agent POC.

Day 2 Status: COMPLETE — all business logic implemented.
              No real APIs are called; every behaviour is simulated
              with in-memory dictionaries and deterministic rules.

Each tool follows this contract:
  ┌─────────────────────────────────────────────────────────────┐
  │  Input  : Pydantic BaseModel  (validated before the call)   │
  │  Output : Pydantic BaseModel  (structured, type-safe)       │
  │  Logic  : Pure Python — no side effects except updating     │
  │           the shared workflow_store dict (check_duplicate,  │
  │           get_invoice_summary) and config.PROCESSED_INVOICES│
  └─────────────────────────────────────────────────────────────┘

Tools
─────
1. validate_vendor    — Is the vendor registered? Does the GSTIN match?
2. validate_po        — Does the PO exist? What is its approved amount?
3. check_duplicate    — Has this invoice already been processed?
4. request_approval   — Stub that marks high-value invoices as "pending".
5. post_to_erp        — Mock ERP post; returns a UUID reference ID.
6. get_invoice_summary — Helper that reads the shared workflow store.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

import app.config as config


# ─────────────────────────────────────────────────────────────────────────────
# Shared In-Memory Workflow Store
# ─────────────────────────────────────────────────────────────────────────────
# Maps  invoice_id  →  dict with snapshot of the most recent call results.
# This is intentionally simple for a POC.
# In production you would use a proper database / state machine.

workflow_store: dict[str, dict] = {}


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
    # Rule 1: Is the vendor name in our master list?
    if input.vendor_name not in config.VALID_VENDORS:
        return ValidateVendorOutput(
            is_valid=False,
            reason=f"Vendor '{input.vendor_name}' is not registered in the system.",
        )

    # Rule 2: Does the GSTIN on the invoice match what we have on record?
    expected_gstin = config.VALID_VENDORS[input.vendor_name]
    if input.vendor_gstin.upper() != expected_gstin.upper():
        return ValidateVendorOutput(
            is_valid=False,
            reason=(
                f"GSTIN mismatch for vendor '{input.vendor_name}'. "
                f"Invoice has '{input.vendor_gstin}', but system has '{expected_gstin}'."
            ),
        )

    # All checks passed
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
    po_amount: float | None = None  # approved amount from the PO (INR)


def validate_po(input: ValidatePOInput) -> ValidatePOOutput:
    """
    Check whether the PO number exists and is active.

    Validation rules
    ─────────────────
    1. po_number must exist in config.VALID_POS.
    2. The approved amount from the PO is returned so the agent can decide
       whether the invoice amount exceeds the PO budget.

    Returns
    ───────
    ValidatePOOutput
        is_valid=True   + po_amount  if the PO exists.
        is_valid=False  + reason     if the PO does not exist.
    """
    if input.po_number not in config.VALID_POS:
        return ValidatePOOutput(
            is_valid=False,
            reason=f"PO number '{input.po_number}' does not exist in the system.",
            po_amount=None,
        )

    approved_amount = config.VALID_POS[input.po_number]
    _update_store(input.invoice_id, {"po_validated": True, "po_number": input.po_number, "po_amount": approved_amount})
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

    Implementation notes
    ────────────────────
    Uses config.PROCESSED_INVOICES (an in-memory set) as the registry.
    When an invoice is NOT a duplicate, it is immediately registered so
    subsequent calls for the same invoice_id correctly return is_duplicate=True.

    In production, this would query a database table instead.

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

    # Not a duplicate — register it now so future calls detect it
    config.PROCESSED_INVOICES.add(input.invoice_id)
    _update_store(input.invoice_id, {"duplicate_checked": True})
    return CheckDuplicateOutput(
        is_duplicate=False,
        reason=f"Invoice '{input.invoice_id}' is not a duplicate. Registered for processing.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — request_approval  (Day 2: STUB)
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

    Day 2 behaviour (STUB)
    ──────────────────────
    Always returns approval_status="pending".
    This simulates the agent raising a flag and waiting for a human.

    Day 4 upgrade
    ─────────────
    This stub will be replaced with a real agents.interrupt() call,
    which pauses the agent execution until a human resumes it with
    an approval or rejection decision.

    Why a stub is useful here
    ─────────────────────────
    Having the stub lets us write and test the full agent decision loop
    end-to-end without needing a real human in the loop yet.

    Returns
    ───────
    ApprovalOutput
        approval_status="pending"  (always, until Day 4)
        notes: explanation of the pending reason
    """
    _update_store(
        input.invoice_id,
        {
            "approval_requested": True,
            "approval_status": "pending",
            "approval_reason": input.reason,
            "approval_amount": input.amount,
        },
    )
    return ApprovalOutput(
        approval_status="pending",
        approver=None,
        notes=(
            f"Approval request raised for invoice '{input.invoice_id}' "
            f"(amount: INR {input.amount:,.2f}). "
            f"Reason: {input.reason}. "
            "Awaiting human decision — will upgrade to agents.interrupt() in Day 4."
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
    Post a validated invoice to the ERP system.

    Mock behaviour
    ──────────────
    • Always succeeds for any non-empty inputs.
    • Generates a unique ERP reference ID using uuid4 so each call
      produces a different, realistic-looking reference.
    • Format: ERP-<8 uppercase hex characters>   e.g. ERP-A3F9C120

    Guardrail note (Day 4)
    ──────────────────────
    In Day 4 an ERPPostGuardrail will be added that blocks this tool
    from firing unless vendor_validated=True and po_validated=True are
    present in the workflow_store. This prevents accidental ERP posts
    when the agent skips validation steps.

    Returns
    ───────
    ERPPostOutput
        success=True + erp_reference_id  on success.
        success=False + reason           if inputs are missing/empty
                                         (defensive check).
    """
    # Defensive validation: all fields must be non-empty
    if not all([input.invoice_id, input.vendor_gstin, input.po_number]):
        return ERPPostOutput(
            success=False,
            erp_reference_id=None,
            reason="ERP post failed: one or more required fields (invoice_id, vendor_gstin, po_number) are empty.",
        )

    # Generate a deterministic-looking reference ID
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
    invoice_id:       str
    status:           str
    tools_called:     list[str]
    approval_status:  str


def get_invoice_summary(input: WorkflowSummaryInput) -> WorkflowSummaryOutput:
    """
    Return a snapshot summary of an invoice's current processing state.

    Data source
    ───────────
    Reads from the module-level `workflow_store` dict that is updated
    by every other tool in this file.

    Usage by the agent
    ──────────────────
    The agent calls this helper to get a bird's-eye view of what has
    happened so far before deciding its next action. This gives the
    LLM structured context instead of having to parse raw tool outputs.

    Returns
    ───────
    WorkflowSummaryOutput
        invoice_id       : echoed back for clarity
        status           : current pipeline status string
        tools_called     : list of tool names executed so far
        approval_status  : "not_required" | "pending" | "approved" | "rejected"
    """
    store = workflow_store.get(input.invoice_id, {})

    # Derive which logical tools have been called from store keys
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

    # Derive overall status
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
