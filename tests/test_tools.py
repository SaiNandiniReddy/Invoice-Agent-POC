"""
tests/test_tools.py — Unit tests for all Phase 2 tool implementations.

HOW TO RUN
──────────
From the project root  (Invoice-Agent-POC/)  run:

    pytest tests/test_tools.py -v

Or to see a quick summary without verbose output:

    pytest tests/test_tools.py

Each test class maps to ONE tool so you can quickly find failing tests:

    TestValidateVendor   → validate_vendor
    TestValidatePO       → validate_po
    TestCheckDuplicate   → check_duplicate
    TestRequestApproval  → request_approval
    TestPostToERP        → post_to_erp
    TestGetInvoiceSummary → get_invoice_summary
    TestWorkflowIntegration → end-to-end happy path through all tools

HOW TESTS ARE STRUCTURED
─────────────────────────
Each test method follows the Arrange / Act / Assert pattern:

    def test_something():
        # ARRANGE – set up inputs
        inp = SomeInput(field="value")

        # ACT – call the function under test
        result = some_tool(inp)

        # ASSERT – check the result
        assert result.is_valid is True
        assert "expected text" in result.reason

This makes each test self-documenting and easy to read.
"""

import pytest

# ── Reset shared state before every test ─────────────────────────────────────
# The tools share two pieces of state between calls:
#   1. app.config.PROCESSED_INVOICES  (set of already-processed invoice IDs)
#   2. app.tools.workflow_store        (dict of invoice snapshots)
#
# We MUST clear them before each test so tests do not affect each other.
# pytest's autouse=True fixture runs this automatically for every test.

import app.config as config
import app.tools as tools_module
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


@pytest.fixture(autouse=True)
def reset_shared_state():
    """
    autouse=True means this fixture runs before EVERY test automatically.
    It clears any leftover state so tests are fully isolated from each other.
    """
    config.PROCESSED_INVOICES.clear()
    tools_module.workflow_store.clear()
    yield  # test runs here
    # (optional teardown after yield — nothing needed for now)


# =============================================================================
# TestValidateVendor — tests for the validate_vendor tool
# =============================================================================

class TestValidateVendor:
    """Tests for validate_vendor — vendor registration and GSTIN matching."""

    def test_valid_vendor_with_correct_gstin(self):
        """
        GIVEN a vendor that exists in config.VALID_VENDORS
         AND  their GSTIN on the invoice matches what the system holds
        WHEN  validate_vendor is called
        THEN  is_valid should be True
        """
        # ARRANGE
        inp = ValidateVendorInput(
            invoice_id="INV-001",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        )

        # ACT
        result = validate_vendor(inp)

        # ASSERT
        assert result.is_valid is True
        assert "Tech Supplies Ltd" in result.reason

    def test_unknown_vendor_is_invalid(self):
        """
        GIVEN a vendor that does NOT exist in config.VALID_VENDORS
        WHEN  validate_vendor is called
        THEN  is_valid should be False and reason should mention the vendor name
        """
        inp = ValidateVendorInput(
            invoice_id="INV-002",
            vendor_name="Fake Vendor Corp",
            vendor_gstin="99ZZZZZ9999Z9Z9",
        )

        result = validate_vendor(inp)

        assert result.is_valid is False
        assert "Fake Vendor Corp" in result.reason

    def test_known_vendor_wrong_gstin_is_invalid(self):
        """
        GIVEN a vendor that IS in the system
         BUT  the GSTIN on the invoice does NOT match what we have on record
        WHEN  validate_vendor is called
        THEN  is_valid should be False and reason should mention 'mismatch'
        """
        inp = ValidateVendorInput(
            invoice_id="INV-003",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="00XXXXX0000X0X0",   # wrong GSTIN
        )

        result = validate_vendor(inp)

        assert result.is_valid is False
        assert "mismatch" in result.reason.lower()

    def test_gstin_comparison_is_case_insensitive(self):
        """
        GIVEN a valid vendor and their GSTIN provided in lowercase
        WHEN  validate_vendor is called
        THEN  validation should still pass (case-insensitive comparison)
        """
        inp = ValidateVendorInput(
            invoice_id="INV-004",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29abcde1234f1z5",   # lowercase version of valid GSTIN
        )

        result = validate_vendor(inp)

        assert result.is_valid is True

    def test_all_three_valid_vendors(self):
        """
        Verify every vendor in the master list passes validation
        with the correct GSTIN.
        """
        vendors = [
            ("Tech Supplies Ltd",  "29ABCDE1234F1Z5"),
            ("Office Mart India",  "27ZYXWV9876G2A3"),
            ("Cloud Services Inc", "07LMNOP5678H3B4"),
        ]
        for i, (name, gstin) in enumerate(vendors):
            inp = ValidateVendorInput(invoice_id=f"INV-V{i}", vendor_name=name, vendor_gstin=gstin)
            result = validate_vendor(inp)
            assert result.is_valid is True, f"Expected valid for {name}"


# =============================================================================
# TestValidatePO — tests for the validate_po tool
# =============================================================================

class TestValidatePO:
    """Tests for validate_po — PO existence and approved amount retrieval."""

    def test_valid_po_returns_amount(self):
        """
        GIVEN a PO number that exists in config.VALID_POS
        WHEN  validate_po is called
        THEN  is_valid is True and po_amount matches the configured amount
        """
        inp = ValidatePOInput(invoice_id="INV-001", po_number="PO-45678")

        result = validate_po(inp)

        assert result.is_valid is True
        assert result.po_amount == 150_000.0

    def test_valid_po_small_amount(self):
        """Test a valid PO with a smaller approved amount."""
        inp = ValidatePOInput(invoice_id="INV-002", po_number="PO-12345")

        result = validate_po(inp)

        assert result.is_valid is True
        assert result.po_amount == 50_000.0

    def test_invalid_po_returns_none_amount(self):
        """
        GIVEN a PO number that does NOT exist in config.VALID_POS
        WHEN  validate_po is called
        THEN  is_valid is False and po_amount is None
        """
        inp = ValidatePOInput(invoice_id="INV-003", po_number="PO-00000")

        result = validate_po(inp)

        assert result.is_valid is False
        assert result.po_amount is None
        assert "PO-00000" in result.reason

    def test_all_three_valid_pos(self):
        """Every PO in the master list should validate successfully."""
        pos = {
            "PO-45678": 150_000.0,
            "PO-12345": 50_000.0,
            "PO-99999": 200_000.0,
        }
        for i, (po_num, expected_amount) in enumerate(pos.items()):
            inp = ValidatePOInput(invoice_id=f"INV-PO{i}", po_number=po_num)
            result = validate_po(inp)
            assert result.is_valid is True
            assert result.po_amount == expected_amount


# =============================================================================
# TestCheckDuplicate — tests for the check_duplicate tool
# =============================================================================

class TestCheckDuplicate:
    """Tests for check_duplicate — duplicate invoice detection."""

    def test_first_occurrence_is_not_duplicate(self):
        """
        GIVEN an invoice_id that has never been processed
        WHEN  check_duplicate is called
        THEN  is_duplicate is False
        """
        inp = CheckDuplicateInput(invoice_id="INV-001")

        result = check_duplicate(inp)

        assert result.is_duplicate is False
        assert "INV-001" in result.reason

    def test_second_occurrence_is_duplicate(self):
        """
        GIVEN check_duplicate was already called once for an invoice
        WHEN  check_duplicate is called again for the SAME invoice_id
        THEN  is_duplicate is True
        """
        # First call — registers the invoice
        check_duplicate(CheckDuplicateInput(invoice_id="INV-001"))

        # Second call — should detect as duplicate
        result = check_duplicate(CheckDuplicateInput(invoice_id="INV-001"))

        assert result.is_duplicate is True
        assert "already been processed" in result.reason.lower()

    def test_different_invoices_are_independent(self):
        """
        GIVEN two different invoice IDs
        WHEN  each is checked for duplicates for the first time
        THEN  both should be flagged as NOT duplicate
        """
        result_a = check_duplicate(CheckDuplicateInput(invoice_id="INV-AAA"))
        result_b = check_duplicate(CheckDuplicateInput(invoice_id="INV-BBB"))

        assert result_a.is_duplicate is False
        assert result_b.is_duplicate is False

    def test_registers_invoice_in_processed_set(self):
        """
        After a successful (non-duplicate) check, the invoice_id
        should be present in config.PROCESSED_INVOICES.
        """
        check_duplicate(CheckDuplicateInput(invoice_id="INV-TRACK"))

        assert "INV-TRACK" in config.PROCESSED_INVOICES

    def test_pre_existing_invoice_in_set_is_duplicate(self):
        """
        If invoice_id is already in config.PROCESSED_INVOICES BEFORE
        check_duplicate is called (e.g. added by another process),
        it should correctly be identified as a duplicate.
        """
        # Simulate a pre-existing entry
        config.PROCESSED_INVOICES.add("INV-PREEXIST")

        result = check_duplicate(CheckDuplicateInput(invoice_id="INV-PREEXIST"))

        assert result.is_duplicate is True


# =============================================================================
# TestRequestApproval — tests for the request_approval stub
# =============================================================================

class TestRequestApproval:
    """Tests for request_approval — Day 2 stub behaviour."""

    def test_approval_status_is_always_pending(self):
        """
        In Day 2 the stub always returns 'pending' regardless of the amount.
        This confirms the stub behaviour before Day 4's real interrupt().
        """
        inp = ApprovalInput(
            invoice_id="INV-001",
            amount=125_000.0,
            reason="Amount exceeds approval threshold",
        )

        result = request_approval(inp)

        assert result.approval_status == "pending"

    def test_approver_is_none_in_stub(self):
        """
        Since no human has made a decision yet (stub only),
        approver should be None.
        """
        inp = ApprovalInput(invoice_id="INV-002", amount=200_000.0, reason="High value invoice")

        result = request_approval(inp)

        assert result.approver is None

    def test_notes_contains_invoice_id(self):
        """The notes field should reference the invoice ID for traceability."""
        inp = ApprovalInput(invoice_id="INV-TRACE", amount=50_000.0, reason="Routine approval")

        result = request_approval(inp)

        assert "INV-TRACE" in result.notes

    def test_approval_stored_in_workflow(self):
        """
        After calling request_approval, the workflow_store should record
        that approval was requested for this invoice.
        """
        inp = ApprovalInput(invoice_id="INV-STORE", amount=99_000.0, reason="Test")

        request_approval(inp)

        assert tools_module.workflow_store["INV-STORE"]["approval_requested"] is True
        assert tools_module.workflow_store["INV-STORE"]["approval_status"] == "pending"


# =============================================================================
# TestPostToERP — tests for the post_to_erp tool
# =============================================================================

class TestPostToERP:
    """Tests for post_to_erp — mock ERP posting."""

    def test_successful_post_returns_reference_id(self):
        """
        GIVEN valid invoice details
        WHEN  post_to_erp is called
        THEN  success is True and erp_reference_id starts with 'ERP-'
        """
        inp = ERPPostInput(
            invoice_id="INV-001",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=45_000.0,
        )

        result = post_to_erp(inp)

        assert result.success is True
        assert result.erp_reference_id is not None
        assert result.erp_reference_id.startswith("ERP-")

    def test_reference_id_is_unique_per_call(self):
        """
        Two separate calls should produce DIFFERENT ERP reference IDs
        (UUID-based generation ensures uniqueness).
        """
        inp = ERPPostInput(
            invoice_id="INV-002",
            vendor_gstin="27ZYXWV9876G2A3",
            po_number="PO-45678",
            amount=75_000.0,
        )

        result1 = post_to_erp(inp)
        result2 = post_to_erp(inp)

        assert result1.erp_reference_id != result2.erp_reference_id

    def test_missing_invoice_id_fails(self):
        """
        If invoice_id is empty, the tool should return success=False
        rather than raising an exception.
        """
        inp = ERPPostInput(
            invoice_id="",           # intentionally empty
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=45_000.0,
        )

        result = post_to_erp(inp)

        assert result.success is False
        assert result.erp_reference_id is None

    def test_reference_id_format(self):
        """ERP reference ID must match the format: ERP-XXXXXXXX (8 hex chars)."""
        inp = ERPPostInput(
            invoice_id="INV-FORMAT",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=1000.0,
        )

        result = post_to_erp(inp)

        # Format: "ERP-" followed by exactly 8 uppercase hex characters
        ref = result.erp_reference_id
        assert len(ref) == 12     # "ERP-" (4) + 8 hex chars
        hex_part = ref[4:]
        assert all(c in "0123456789ABCDEF" for c in hex_part)

    def test_erp_post_recorded_in_workflow_store(self):
        """After a successful ERP post, workflow_store should record the reference ID."""
        inp = ERPPostInput(
            invoice_id="INV-WF",
            vendor_gstin="07LMNOP5678H3B4",
            po_number="PO-99999",
            amount=180_000.0,
        )

        result = post_to_erp(inp)

        store = tools_module.workflow_store.get("INV-WF", {})
        assert store.get("erp_posted") is True
        assert store.get("erp_reference_id") == result.erp_reference_id


# =============================================================================
# TestGetInvoiceSummary — tests for the get_invoice_summary helper
# =============================================================================

class TestGetInvoiceSummary:
    """Tests for get_invoice_summary — workflow state snapshot."""

    def test_unknown_invoice_returns_pending_status(self):
        """
        GIVEN an invoice_id that has not been processed at all
        WHEN  get_invoice_summary is called
        THEN  status should be 'pending' and tools_called should be empty
        """
        result = get_invoice_summary(WorkflowSummaryInput(invoice_id="INV-UNKNOWN"))

        assert result.status == "pending"
        assert result.tools_called == []
        assert result.approval_status == "not_required"

    def test_summary_after_vendor_validation(self):
        """After validate_vendor succeeds, tools_called should include it."""
        validate_vendor(ValidateVendorInput(
            invoice_id="INV-SUM",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        ))

        result = get_invoice_summary(WorkflowSummaryInput(invoice_id="INV-SUM"))

        assert "validate_vendor" in result.tools_called
        assert result.status == "in_progress"

    def test_summary_after_approval_request(self):
        """
        After request_approval is called, status should be 'awaiting_approval'
        and approval_status should be 'pending'.
        """
        request_approval(ApprovalInput(
            invoice_id="INV-APP",
            amount=200_000.0,
            reason="Test",
        ))

        result = get_invoice_summary(WorkflowSummaryInput(invoice_id="INV-APP"))

        assert result.status == "awaiting_approval"
        assert result.approval_status == "pending"
        assert "request_approval" in result.tools_called

    def test_summary_after_erp_post(self):
        """After post_to_erp succeeds, status should be 'completed'."""
        post_to_erp(ERPPostInput(
            invoice_id="INV-DONE",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=45_000.0,
        ))

        result = get_invoice_summary(WorkflowSummaryInput(invoice_id="INV-DONE"))

        assert result.status == "completed"
        assert "post_to_erp" in result.tools_called


# =============================================================================
# TestWorkflowIntegration — end-to-end happy path
# =============================================================================

class TestWorkflowIntegration:
    """
    Integration test: runs all tools in order for the 'happy path' scenario.

    This simulates what the agent does when processing invoice_happy.json
    (Tech Supplies Ltd, PO-12345, INR 45,000 — no approval required).
    """

    def test_happy_path_all_tools_in_sequence(self):
        """
        Full pipeline:
          validate_vendor → validate_po → check_duplicate → post_to_erp
          → get_invoice_summary

        All steps should succeed and the final summary should show
        status='completed' with all relevant tools recorded.
        """
        INVOICE_ID = "INV-2026-001"

        # Step 1 — Validate vendor
        vendor_result = validate_vendor(ValidateVendorInput(
            invoice_id=INVOICE_ID,
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        ))
        assert vendor_result.is_valid is True, "Step 1 failed: vendor should be valid"

        # Step 2 — Validate PO
        po_result = validate_po(ValidatePOInput(
            invoice_id=INVOICE_ID,
            po_number="PO-12345",
        ))
        assert po_result.is_valid is True, "Step 2 failed: PO should be valid"
        assert po_result.po_amount == 50_000.0

        # Step 3 — Check duplicate
        dup_result = check_duplicate(CheckDuplicateInput(invoice_id=INVOICE_ID))
        assert dup_result.is_duplicate is False, "Step 3 failed: should not be a duplicate"

        # Step 4 — Post to ERP (no approval needed — amount 45k < threshold 100k)
        erp_result = post_to_erp(ERPPostInput(
            invoice_id=INVOICE_ID,
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=45_000.0,
        ))
        assert erp_result.success is True, "Step 4 failed: ERP post should succeed"
        assert erp_result.erp_reference_id is not None

        # Step 5 — Get summary
        summary = get_invoice_summary(WorkflowSummaryInput(invoice_id=INVOICE_ID))
        assert summary.status == "completed"
        assert "validate_vendor" in summary.tools_called
        assert "validate_po" in summary.tools_called
        assert "check_duplicate" in summary.tools_called
        assert "post_to_erp" in summary.tools_called

    def test_failure_path_unknown_vendor(self):
        """
        Pipeline for invoice_failure.json — unknown vendor, invalid PO.
        validate_vendor should fail immediately.
        """
        INVOICE_ID = "INV-2026-003"

        vendor_result = validate_vendor(ValidateVendorInput(
            invoice_id=INVOICE_ID,
            vendor_name="Fake Vendor Corp",
            vendor_gstin="99ZZZZZ9999Z9Z9",
        ))

        assert vendor_result.is_valid is False
        assert "not registered" in vendor_result.reason.lower()

    def test_approval_path_high_value_invoice(self):
        """
        Pipeline for invoice_approval.json — Tech Supplies Ltd, INR 125,000.
        Amount > 100,000 threshold → request_approval should be called.
        """
        INVOICE_ID = "INV-2026-002"

        # Validate first (all pass)
        validate_vendor(ValidateVendorInput(
            invoice_id=INVOICE_ID,
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        ))
        validate_po(ValidatePOInput(invoice_id=INVOICE_ID, po_number="PO-45678"))
        check_duplicate(CheckDuplicateInput(invoice_id=INVOICE_ID))

        # High value — request approval
        approval_result = request_approval(ApprovalInput(
            invoice_id=INVOICE_ID,
            amount=125_000.0,
            reason="Invoice amount exceeds INR 1,00,000 threshold",
        ))

        assert approval_result.approval_status == "pending"

        # Summary should show awaiting_approval (ERP not posted yet)
        summary = get_invoice_summary(WorkflowSummaryInput(invoice_id=INVOICE_ID))
        assert summary.status == "awaiting_approval"
        assert summary.approval_status == "pending"
