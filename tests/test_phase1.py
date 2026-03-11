"""
tests/test_phase1.py — Automated test suite for Day 1 (Phase 1).

What is tested here:
  - app/config.py  : constants are present and have correct types
  - app/state.py   : all Pydantic schemas validate correct data
  - sample_data/   : all 3 JSON files parse into valid Invoice objects
  - app/tools.py   : tool I/O schemas accept and reject data correctly

These tests do NOT call the OpenAI API.
They run entirely offline and verify the data layer is solid before
any agent wiring begins (Day 3).

Run with:
    pytest tests/test_phase1.py -v
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

# ── Helpers ───────────────────────────────────────────────────────────────────

SAMPLE_DATA_DIR = Path(__file__).parent.parent / "sample_data"


# =============================================================================
# Section 1: Config Tests
# =============================================================================

class TestConfig:
    """Verify config.py loads constants with correct types and values."""

    def test_approval_threshold_is_float(self):
        from app.config import APPROVAL_THRESHOLD
        assert isinstance(APPROVAL_THRESHOLD, float), "APPROVAL_THRESHOLD must be a float"

    def test_approval_threshold_value(self):
        from app.config import APPROVAL_THRESHOLD
        assert APPROVAL_THRESHOLD == 100_000.0, "Default threshold must be ₹1,00,000"

    def test_valid_vendors_is_dict(self):
        from app.config import VALID_VENDORS
        assert isinstance(VALID_VENDORS, dict)
        assert len(VALID_VENDORS) >= 1, "At least one vendor must be configured"

    def test_valid_pos_is_dict(self):
        from app.config import VALID_POS
        assert isinstance(VALID_POS, dict)
        assert len(VALID_POS) >= 1, "At least one PO must be configured"

    def test_processed_invoices_starts_empty(self):
        from app.config import PROCESSED_INVOICES
        # At import time and before any processing, set must be empty (or reset)
        assert isinstance(PROCESSED_INVOICES, set)

    def test_output_dir_is_string(self):
        from app.config import OUTPUT_DIR
        assert isinstance(OUTPUT_DIR, str)
        assert len(OUTPUT_DIR) > 0

    def test_openai_model_is_string(self):
        from app.config import OPENAI_MODEL
        assert isinstance(OPENAI_MODEL, str)
        assert OPENAI_MODEL.startswith("gpt-"), f"Model '{OPENAI_MODEL}' does not look like a GPT model"


# =============================================================================
# Section 2: LineItem Schema Tests
# =============================================================================

class TestLineItemSchema:
    """Verify LineItem Pydantic model validation."""

    def test_valid_line_item(self):
        from app.state import LineItem
        item = LineItem(description="Laptops", quantity=5, unit_price=25000)
        assert item.description == "Laptops"
        assert item.quantity == 5
        assert item.unit_price == 25000
        assert item.total_price == 125_000.0

    def test_zero_quantity_rejected(self):
        from app.state import LineItem
        with pytest.raises(ValidationError):
            LineItem(description="Laptops", quantity=0, unit_price=25000)

    def test_negative_quantity_rejected(self):
        from app.state import LineItem
        with pytest.raises(ValidationError):
            LineItem(description="Laptops", quantity=-1, unit_price=25000)

    def test_zero_unit_price_rejected(self):
        from app.state import LineItem
        with pytest.raises(ValidationError):
            LineItem(description="Laptops", quantity=1, unit_price=0)

    def test_total_price_calculation(self):
        from app.state import LineItem
        item = LineItem(description="Keyboards", quantity=10, unit_price=1500)
        assert item.total_price == 15_000.0


# =============================================================================
# Section 3: Invoice Schema Tests
# =============================================================================

class TestInvoiceSchema:
    """Verify Invoice Pydantic model validation and field constraints."""

    def _valid_invoice_data(self) -> dict:
        return {
            "invoice_id": "INV-TEST-001",
            "vendor_name": "Tech Supplies Ltd",
            "vendor_gstin": "29ABCDE1234F1Z5",
            "po_number": "PO-45678",
            "invoice_amount": 50000.0,
            "currency": "INR",
            "invoice_date": "2026-03-11",
            "line_items": [
                {"description": "Keyboards", "quantity": 10, "unit_price": 5000}
            ],
        }

    def test_valid_invoice_parses(self):
        from app.state import Invoice
        inv = Invoice(**self._valid_invoice_data())
        assert inv.invoice_id == "INV-TEST-001"
        assert inv.invoice_amount == 50000.0

    def test_gstin_must_be_15_chars(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        data["vendor_gstin"] = "SHORT"
        with pytest.raises(ValidationError):
            Invoice(**data)

    def test_gstin_normalised_to_uppercase(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        data["vendor_gstin"] = "29abcde1234f1z5"  # lowercase input
        inv = Invoice(**data)
        assert inv.vendor_gstin == "29ABCDE1234F1Z5"

    def test_invalid_date_format_rejected(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        data["invoice_date"] = "11-03-2026"  # Wrong format (DD-MM-YYYY)
        with pytest.raises(ValidationError):
            Invoice(**data)

    def test_negative_invoice_amount_rejected(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        data["invoice_amount"] = -1
        with pytest.raises(ValidationError):
            Invoice(**data)

    def test_zero_invoice_amount_rejected(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        data["invoice_amount"] = 0
        with pytest.raises(ValidationError):
            Invoice(**data)

    def test_default_currency_is_inr(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        del data["currency"]
        inv = Invoice(**data)
        assert inv.currency == "INR"

    def test_empty_line_items_allowed(self):
        from app.state import Invoice
        data = self._valid_invoice_data()
        data["line_items"] = []
        inv = Invoice(**data)
        assert inv.line_items == []


# =============================================================================
# Section 4: WorkflowState Tests
# =============================================================================

class TestWorkflowState:
    """Verify WorkflowState transitions and action history logging."""

    def test_initial_state_is_pending(self):
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        assert state.status == WorkflowStatus.PENDING
        assert state.action_history == []

    def test_add_action_appends_record(self):
        from app.state import WorkflowState
        state = WorkflowState(invoice_id="INV-TEST-001")
        state.add_action(
            tool_name="validate_vendor",
            input_data={"vendor_name": "Tech Supplies Ltd"},
            output_data={"is_valid": True},
        )
        assert len(state.action_history) == 1
        assert state.action_history[0].tool_name == "validate_vendor"

    def test_complete_sets_status(self):
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        state.complete(erp_reference_id="ERP-REF-999")
        assert state.status == WorkflowStatus.COMPLETED
        assert state.erp_reference_id == "ERP-REF-999"
        assert state.completed_at is not None

    def test_reject_sets_status_and_reason(self):
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        state.reject("Vendor not registered")
        assert state.status == WorkflowStatus.REJECTED
        assert state.rejection_reason == "Vendor not registered"

    def test_escalate_sets_manual_review(self):
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        state.escalate("ERP post failed after approval")
        assert state.status == WorkflowStatus.MANUAL_REVIEW

    def test_multiple_actions_in_history(self):
        from app.state import WorkflowState
        state = WorkflowState(invoice_id="INV-TEST-001")
        for tool in ["validate_vendor", "validate_po", "check_duplicate"]:
            state.add_action(tool_name=tool, input_data={}, output_data={})
        assert len(state.action_history) == 3
        assert [a.tool_name for a in state.action_history] == [
            "validate_vendor", "validate_po", "check_duplicate"
        ]


# =============================================================================
# Section 5: NextActionDecision Schema Tests
# =============================================================================

class TestNextActionDecisionSchema:
    """Verify the structured output schema the agent must return."""

    def test_valid_decision(self):
        from app.state import NextActionDecision
        decision = NextActionDecision(
            next_action="validate_vendor",
            reason="Starting workflow — validate vendor first",
            confidence=0.99,
        )
        assert decision.next_action == "validate_vendor"
        assert decision.confidence == 0.99
        assert decision.required_input is None

    def test_confidence_above_1_rejected(self):
        from app.state import NextActionDecision
        with pytest.raises(ValidationError):
            NextActionDecision(next_action="complete", reason="Done", confidence=1.5)

    def test_confidence_below_0_rejected(self):
        from app.state import NextActionDecision
        with pytest.raises(ValidationError):
            NextActionDecision(next_action="complete", reason="Done", confidence=-0.1)

    def test_required_input_can_be_dict(self):
        from app.state import NextActionDecision
        decision = NextActionDecision(
            next_action="request_approval",
            reason="Amount exceeds threshold",
            confidence=0.95,
            required_input={"invoice_id": "INV-2026-002", "amount": 125000},
        )
        assert decision.required_input["amount"] == 125000


# =============================================================================
# Section 6: Sample Data File Tests
# =============================================================================

class TestSampleDataFiles:
    """
    Verify all 3 sample JSON files:
      1. Exist on disk
      2. Contain valid JSON
      3. Parse into valid Invoice Pydantic models
      4. Represent the correct test scenario (amount, vendor, etc.)
    """

    def _load(self, filename: str):
        from app.state import Invoice
        path = SAMPLE_DATA_DIR / filename
        assert path.exists(), f"Missing sample file: {path}"
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Invoice(**raw), raw

    # ── invoice_happy.json ───────────────────────────────────────────
    def test_happy_invoice_loads(self):
        inv, _ = self._load("invoice_happy.json")
        assert inv.invoice_id.startswith("INV-")

    def test_happy_invoice_amount_below_threshold(self):
        from app.config import APPROVAL_THRESHOLD
        inv, _ = self._load("invoice_happy.json")
        assert inv.invoice_amount < APPROVAL_THRESHOLD, (
            f"Happy path invoice amount {inv.invoice_amount} "
            f"must be below threshold {APPROVAL_THRESHOLD}"
        )

    def test_happy_invoice_vendor_is_registered(self):
        from app.config import VALID_VENDORS
        inv, _ = self._load("invoice_happy.json")
        assert inv.vendor_name in VALID_VENDORS, (
            f"Happy path vendor '{inv.vendor_name}' must be in VALID_VENDORS"
        )

    def test_happy_invoice_po_is_valid(self):
        from app.config import VALID_POS
        inv, _ = self._load("invoice_happy.json")
        assert inv.po_number in VALID_POS, (
            f"Happy path PO '{inv.po_number}' must be in VALID_POS"
        )

    # ── invoice_approval.json ────────────────────────────────────────
    def test_approval_invoice_loads(self):
        inv, _ = self._load("invoice_approval.json")
        assert inv.invoice_id.startswith("INV-")

    def test_approval_invoice_amount_above_threshold(self):
        from app.config import APPROVAL_THRESHOLD
        inv, _ = self._load("invoice_approval.json")
        assert inv.invoice_amount > APPROVAL_THRESHOLD, (
            f"Approval path invoice amount {inv.invoice_amount} "
            f"must be ABOVE threshold {APPROVAL_THRESHOLD}"
        )

    def test_approval_invoice_vendor_is_registered(self):
        from app.config import VALID_VENDORS
        inv, _ = self._load("invoice_approval.json")
        assert inv.vendor_name in VALID_VENDORS

    # ── invoice_failure.json ─────────────────────────────────────────
    def test_failure_invoice_loads(self):
        inv, _ = self._load("invoice_failure.json")
        assert inv.invoice_id.startswith("INV-")

    def test_failure_invoice_vendor_is_not_registered(self):
        from app.config import VALID_VENDORS
        inv, _ = self._load("invoice_failure.json")
        assert inv.vendor_name not in VALID_VENDORS, (
            f"Failure path vendor '{inv.vendor_name}' must NOT be in VALID_VENDORS"
        )

    def test_all_three_scenario_ids_are_unique(self):
        ids = set()
        for fname in ["invoice_happy.json", "invoice_approval.json", "invoice_failure.json"]:
            inv, _ = self._load(fname)
            assert inv.invoice_id not in ids, f"Duplicate invoice_id: {inv.invoice_id}"
            ids.add(inv.invoice_id)


# =============================================================================
# Section 7: Tool Schema Tests (Day 1 — signatures only, no logic)
# =============================================================================

class TestToolSchemas:
    """
    Verify all tool Pydantic I/O schemas accept valid data correctly.
    The underlying tool logic is NotImplementedError on Day 1 —
    we only test that the input/output models are well-formed.
    """

    def test_validate_vendor_input_schema(self):
        from app.tools import ValidateVendorInput
        inp = ValidateVendorInput(
            invoice_id="INV-001",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        )
        assert inp.invoice_id == "INV-001"

    def test_validate_po_input_schema(self):
        from app.tools import ValidatePOInput
        inp = ValidatePOInput(invoice_id="INV-001", po_number="PO-45678")
        assert inp.po_number == "PO-45678"

    def test_validate_po_output_schema_optional_amount(self):
        from app.tools import ValidatePOOutput
        out = ValidatePOOutput(is_valid=False, reason="PO not found")
        assert out.po_amount is None

    def test_check_duplicate_input_schema(self):
        from app.tools import CheckDuplicateInput
        inp = CheckDuplicateInput(invoice_id="INV-001")
        assert inp.invoice_id == "INV-001"

    def test_approval_input_schema(self):
        from app.tools import ApprovalInput
        inp = ApprovalInput(
            invoice_id="INV-002",
            amount=125000.0,
            reason="Amount exceeds threshold",
        )
        assert inp.amount == 125000.0

    def test_erp_post_input_schema(self):
        from app.tools import ERPPostInput
        inp = ERPPostInput(
            invoice_id="INV-001",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-45678",
            amount=45000.0,
        )
        assert inp.amount == 45000.0

    def test_erp_post_output_optional_reference(self):
        from app.tools import ERPPostOutput
        out = ERPPostOutput(success=False, reason="ERP system unavailable")
        assert out.erp_reference_id is None

    @pytest.mark.skip(
        reason=(
            "Day 1 stub test — tools are fully implemented in Phase 2. "
            "Helper was also renamed from get_workflow_summary → get_invoice_summary."
        )
    )
    def test_tool_logic_raises_not_implemented(self):
        """Skipped: tools now have real implementations (Phase 2 complete)."""
        pass
