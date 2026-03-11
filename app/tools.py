"""
app/tools.py — Mock tool implementations for the Invoice Workflow Agent POC.

Day 1 Status: SKELETON — function signatures and Pydantic I/O models defined.
              Business logic is implemented on Day 2.

Each tool is a plain Python function with:
  - A Pydantic BaseModel for input
  - A Pydantic BaseModel for output
  - Type hints throughout
  - Docstring describing what the tool does
"""

from pydantic import BaseModel


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1: validate_vendor
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
    Validates both vendor_name and vendor_gstin against the master list.
    """
    # TODO (Day 2): Implement lookup against config.VALID_VENDORS
    raise NotImplementedError("Implement in Day 2")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2: validate_po
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
    Returns the approved PO amount if valid.
    """
    # TODO (Day 2): Implement lookup against config.VALID_POS
    raise NotImplementedError("Implement in Day 2")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3: check_duplicate
# ─────────────────────────────────────────────────────────────────────────────

class CheckDuplicateInput(BaseModel):
    invoice_id: str


class CheckDuplicateOutput(BaseModel):
    is_duplicate: bool
    reason:       str


def check_duplicate(input: CheckDuplicateInput) -> CheckDuplicateOutput:
    """
    Detect whether this invoice_id has already been processed.
    Uses an in-memory set for the POC (database in production).
    """
    # TODO (Day 2): Implement lookup + registration in config.PROCESSED_INVOICES
    raise NotImplementedError("Implement in Day 2")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4: request_approval
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalInput(BaseModel):
    invoice_id: str
    amount:     float
    reason:     str


class ApprovalOutput(BaseModel):
    approval_status: str          # "pending" | "approved" | "rejected"
    approver:        str | None = None
    notes:           str | None = None


def request_approval(input: ApprovalInput) -> ApprovalOutput:
    """
    Request human approval for high-value invoices.
    Day 2: Returns 'pending' stub.
    Day 4: Upgraded to use SDK interrupt() for real pause/resume.
    """
    # TODO (Day 2): Return stub ApprovalOutput(approval_status="pending")
    # TODO (Day 4): Replace with agents.interrupt() call
    raise NotImplementedError("Implement in Day 2")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5: post_to_erp
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
    Post the validated invoice to the ERP system.
    Mock implementation: always succeeds for valid inputs.
    Guarded by ERPPostGuardrail (Day 4).
    """
    # TODO (Day 2): Implement mock ERP post with UUID reference generation
    raise NotImplementedError("Implement in Day 2")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6 (Helper): get_workflow_summary
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowSummaryInput(BaseModel):
    invoice_id: str


class WorkflowSummaryOutput(BaseModel):
    invoice_id:      str
    status:          str
    tools_called:    list[str]
    approval_status: str


def get_workflow_summary(input: WorkflowSummaryInput) -> WorkflowSummaryOutput:
    """
    Return a human-readable summary of the current workflow state.
    Used by the agent for audit / decision context.
    """
    # TODO (Day 2): Implement by reading from a shared WorkflowState store
    raise NotImplementedError("Implement in Day 2")
