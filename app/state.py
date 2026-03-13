"""
app/state.py — Pydantic schemas for invoices and workflow state.

These models define the data contracts used throughout the entire
Invoice Workflow Agent POC:
  - Invoice / LineItem  : Input data (matches the assignment JSON spec)
  - WorkflowState       : Tracks the current processing status & history
  - ActionRecord        : A single entry in the action history log
  - NextActionDecision  : Structured output schema the agent must return
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ─────────────────────────────────────────────────────────────

class WorkflowStatus(str, Enum):
    """Terminal and intermediate states for an invoice workflow."""
    PENDING       = "pending"
    IN_PROGRESS   = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED     = "completed"
    REJECTED      = "rejected"
    MANUAL_REVIEW = "manual_review"


class ApprovalStatus(str, Enum):
    """Possible states for the human-approval gate."""
    NOT_REQUIRED = "not_required"
    PENDING      = "pending"
    APPROVED     = "approved"
    REJECTED     = "rejected"


# ── Invoice Input Schema ──────────────────────────────────────────────────────

class LineItem(BaseModel):
    """A single line item on an invoice."""
    description: str
    quantity:    int   = Field(gt=0, description="Must be a positive integer")
    unit_price:  float = Field(gt=0, description="Price per unit in INR")

    @property
    def total_price(self) -> float:
        return self.quantity * self.unit_price


class Invoice(BaseModel):
    """
    Full invoice input schema.
    Matches the JSON structure specified in the assignment document.
    """
    invoice_id:     str        = Field(..., description="Unique invoice identifier, e.g. INV-2026-001")
    vendor_name:    str        = Field(..., description="Registered vendor name")
    vendor_gstin:   str        = Field(..., description="15-character GST identification number")
    po_number:      str        = Field(..., description="Purchase order number, e.g. PO-45678")
    invoice_amount: float      = Field(gt=0, description="Total invoice amount in INR")
    currency:       str        = Field(default="INR")
    invoice_date:   str        = Field(..., description="Invoice date in YYYY-MM-DD format")
    line_items:     list[LineItem] = Field(default_factory=list)

    @field_validator("vendor_gstin")
    @classmethod
    def validate_gstin_length(cls, v: str) -> str:
        if len(v) != 15:
            raise ValueError(f"GSTIN must be exactly 15 characters, got {len(v)}")
        return v.upper()

    @field_validator("invoice_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("invoice_date must be in YYYY-MM-DD format")
        return v


# ── Action History ────────────────────────────────────────────────────────────

class ActionRecord(BaseModel):
    """
    Immutable record of a single tool call made during workflow execution.
    Used to build the auditable action history log.
    """
    tool_name:  str              = Field(..., description="Name of the tool that was called")
    timestamp:  str              = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    input_data: dict[str, Any]   = Field(default_factory=dict)
    output_data: dict[str, Any]  = Field(default_factory=dict)
    success:    bool             = True
    notes:      str | None       = None


# ── Workflow State ────────────────────────────────────────────────────────────

class WorkflowState(BaseModel):
    """
    Mutable state object that tracks the entire lifecycle of an invoice workflow.
    Persisted to output/<invoice_id>_result.json after completion.
    """
    invoice_id:      str
    status:          WorkflowStatus     = WorkflowStatus.PENDING
    approval_status: ApprovalStatus     = ApprovalStatus.NOT_REQUIRED
    approver:        str | None         = None
    approval_notes:  str | None         = None
    action_history:  list[ActionRecord] = Field(default_factory=list)
    started_at:      str                = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at:    str | None         = None
    rejection_reason: str | None        = None
    erp_reference_id: str | None        = None

    def add_action(
        self,
        tool_name:   str,
        input_data:  dict[str, Any],
        output_data: dict[str, Any],
        success:     bool = True,
        notes:       str | None = None,
    ) -> None:
        """Append a new action record to the history log."""
        self.action_history.append(
            ActionRecord(
                tool_name=tool_name,
                input_data=input_data,
                output_data=output_data,
                success=success,
                notes=notes,
            )
        )

    def complete(self, erp_reference_id: str | None = None) -> None:
        """Mark the workflow as successfully completed."""
        self.status = WorkflowStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.erp_reference_id = erp_reference_id

    def reject(self, reason: str) -> None:
        """Mark the workflow as rejected with a clear reason."""
        self.status = WorkflowStatus.REJECTED
        self.rejection_reason = reason
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def escalate(self, reason: str) -> None:
        """Escalate to manual review."""
        self.status = WorkflowStatus.MANUAL_REVIEW
        self.rejection_reason = reason
        self.completed_at = datetime.now(timezone.utc).isoformat()



# ── Agent Structured Output Schema ───────────────────────────────────────────

class NextActionDecision(BaseModel):
    """
    Structured output schema the agent MUST return at every decision point.

    The agent loop reads this to determine what to do next.
    Using a strict Pydantic schema ensures machine-readable, type-safe decisions
    rather than free-form text outputs.

    next_action:    What the agent wants to do next.
                    Valid values: validate_vendor | validate_po | check_duplicate
                                  request_approval | post_to_erp
                                  complete | rejected | manual_review
    reason:         Human-readable explanation for this decision.
    confidence:     Agent's self-reported confidence (0.0 — 1.0).
    required_input: Optional extra data needed for the next tool call.
    """
    next_action:    str        = Field(..., description="The next workflow action to take")
    reason:         str        = Field(..., description="Explanation for this decision")
    confidence:     float      = Field(..., ge=0.0, le=1.0, description="Agent confidence 0-1")
    required_input: dict[str, Any] | None = Field(
        default=None,
        description="Any additional input parameters required for the next step"
    )
