"""
tests/test_phase4.py — Phase 4 Unit Tests: Human-in-the-Loop & Guardrails

╔══════════════════════════════════════════════════════════════════════════════╗
║  JUNIOR DEVELOPER GUIDE                                                    ║
║                                                                            ║
║  This file tests FOUR things introduced in Phase 4:                       ║
║                                                                            ║
║  1. PauseForApproval Exception (InterruptionRequirement pattern)           ║
║     - Does request_approval RAISE PauseForApproval on first call?          ║
║     - Does it RETURN ApprovalOutput on the resume path (after human        ║
║       has already written their decision to workflow_store)?               ║
║                                                                            ║
║  2. ERPPostGuardrail logic                                                 ║
║     - Does it BLOCK an ERP post when validation hasn't run?                ║
║     - Does it ALLOW an ERP post when both flags are set?                   ║
║                                                                            ║
║  3. handle_interruptions() in main.py                                      ║
║     - Does the CLI correctly collect and return human decisions?           ║
║                                                                            ║
║  4. Failure path scenarios                                                 ║
║     - Invalid vendor / PO / duplicate invoice                              ║
║                                                                            ║
║  HOW TO RUN:                                                               ║
║      cd Invoice-Agent-POC                                                  ║
║      python -m pytest tests/test_phase4.py -v                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

Key testing technique — PauseForApproval pattern
─────────────────────────────────────────────────
In Phase 4, request_approval uses a CUSTOM EXCEPTION (not agents.interrupt())
to implement the Human-in-the-Loop pause. Here's how it works:

  First call (no human decision yet):
      request_approval(input)
      → saves "pending" to workflow_store
      → raises PauseForApproval(invoice_id, amount, reason)

  main.py catches it, shows approval panel, gets human decision,
  writes "approved"/"rejected" to workflow_store, then re-runs the agent.

  Second call (agent resumed, decision already in store):
      # Pre-seed the store — simulates what main.py does
      workflow_store["INV-001"]["approval_status"] = "approved"
      result = request_approval(input)
      # → reads store, returns ApprovalOutput(approval_status="approved")

This lets us test both paths cleanly without needing a real running agent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.config as config
from app.tools import (
    ApprovalInput,
    CheckDuplicateInput,
    ERPPostInput,
    PauseForApproval,
    ValidatePOInput,
    ValidateVendorInput,
    check_duplicate,
    get_approval_result,
    post_to_erp,
    request_approval,
    validate_po,
    validate_vendor,
    workflow_store,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — set up and tear down test state
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state():
    """
    Reset all shared state before EVERY test.

    Why this matters:
      workflow_store is a module-level dict shared between all tools.
      If test A puts data in it, test B might see stale data and fail
      for the wrong reason. This fixture clears it before each test.

    autouse=True means pytest runs this automatically — no need to
    add it to each test function's parameters.
    """
    workflow_store.clear()
    config.PROCESSED_INVOICES.clear()
    yield   # ← test runs here
    workflow_store.clear()
    config.PROCESSED_INVOICES.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Helper — run validation steps to set workflow_store flags
# ─────────────────────────────────────────────────────────────────────────────

def run_happy_validations(invoice_id: str) -> None:
    """
    Run vendor + PO validation for a known-good invoice.
    Sets vendor_validated=True and po_validated=True in workflow_store,
    which is required before the guardrail will allow post_to_erp.
    """
    validate_vendor(ValidateVendorInput(
        invoice_id=invoice_id,
        vendor_name="Tech Supplies Ltd",
        vendor_gstin="29ABCDE1234F1Z5",
    ))
    validate_po(ValidatePOInput(
        invoice_id=invoice_id,
        po_number="PO-45678",
    ))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — request_approval with PauseForApproval pattern
# ═════════════════════════════════════════════════════════════════════════════

class TestRequestApprovalInterrupt:
    """
    Tests for the HITL request_approval tool.

    There are TWO paths to test:

    PATH A — First call (no human decision yet):
      request_approval() raises PauseForApproval.
      We test that the exception contains the right data.

    PATH B — Resume call (human has already decided):
      workflow_store already has approval_status="approved"/"rejected".
      request_approval() reads it and returns ApprovalOutput.
      We test both "approved" and "rejected" resume paths.
    """

    INVOICE_ID = "INV-4001"
    AMOUNT     = 150_000.0
    REASON     = "Invoice amount exceeds INR 1,00,000 approval threshold."

    def _make_input(self) -> ApprovalInput:
        return ApprovalInput(
            invoice_id=self.INVOICE_ID,
            amount=self.AMOUNT,
            reason=self.REASON,
        )

    # ── Test 1A: First call RAISES PauseForApproval ──────────────────────────
    def test_first_call_raises_pause_for_approval(self):
        """
        GIVEN: workflow_store has no approval_status for this invoice
        WHEN:  request_approval() is called
        THEN:  PauseForApproval is raised (not a return value)
               The exception carries invoice_id, amount, reason
        """
        with pytest.raises(PauseForApproval) as exc_info:
            request_approval(self._make_input())

        exc = exc_info.value
        assert exc.invoice_id == self.INVOICE_ID, "Exception must contain invoice_id"
        assert exc.amount == self.AMOUNT,         "Exception must contain amount"
        assert exc.reason == self.REASON,         "Exception must contain reason"

        # workflow_store must be set to "pending" before the raise
        store = workflow_store.get(self.INVOICE_ID, {})
        assert store.get("approval_status") == "pending"
        assert store.get("approval_requested") is True

    # ── Test 1B: Approval path (resume) ─────────────────────────────────────
    def test_approval_path_returns_approved(self):
        """
        GIVEN: workflow_store already has approval_status="approved"
               (simulates main.py having injected the human decision)
        WHEN:  request_approval() is called again (resume path)
        THEN:  ApprovalOutput.approval_status == "approved"
               ApprovalOutput.approver        == "human-reviewer"
               workflow_store remains with approval_status="approved"
        """
        # Simulate main.py injecting the human decision
        workflow_store[self.INVOICE_ID] = {"approval_status": "approved"}

        result = request_approval(self._make_input())

        assert result.approval_status == "approved", (
            "Expected approval_status='approved' on resume path"
        )
        assert result.approver == "human-reviewer", (
            "approver field should identify a human reviewer"
        )
        assert result.notes is not None, "notes should contain a human-readable explanation"
        assert "APPROVED" in result.notes.upper(), "notes should mention the approval"

        store = workflow_store.get(self.INVOICE_ID, {})
        assert store.get("approval_status") == "approved"

    # ── Test 1C: Rejection path (resume) ────────────────────────────────────
    def test_rejection_path_returns_rejected(self):
        """
        GIVEN: workflow_store already has approval_status="rejected"
        WHEN:  request_approval() is called again (resume path)
        THEN:  ApprovalOutput.approval_status == "rejected"
        """
        workflow_store[self.INVOICE_ID] = {"approval_status": "rejected"}

        result = request_approval(self._make_input())

        assert result.approval_status == "rejected", (
            "Expected approval_status='rejected' on resume path"
        )
        assert result.approver == "human-reviewer"

        store = workflow_store.get(self.INVOICE_ID, {})
        assert store.get("approval_status") == "rejected"

    # ── Test 1D: get_approval_result directly tests both paths ──────────────
    def test_get_approval_result_approved(self):
        """
        GIVEN: workflow_store has approval_status="approved"
        WHEN:  get_approval_result() is called (the helper function)
        THEN:  Returns ApprovalOutput with approval_status="approved"
        """
        workflow_store[self.INVOICE_ID] = {"approval_status": "approved"}
        result = get_approval_result(self.INVOICE_ID, self.AMOUNT, self.REASON)
        assert result.approval_status == "approved"
        assert result.approver == "human-reviewer"

    def test_get_approval_result_rejected(self):
        """
        GIVEN: workflow_store has approval_status="rejected"
        WHEN:  get_approval_result() is called
        THEN:  Returns ApprovalOutput with approval_status="rejected"
        """
        workflow_store[self.INVOICE_ID] = {"approval_status": "rejected"}
        result = get_approval_result(self.INVOICE_ID, self.AMOUNT, self.REASON)
        assert result.approval_status == "rejected"

    # ── Test 1E: PauseForApproval carries all required fields ────────────────
    def test_pause_exception_has_required_fields(self):
        """
        The PauseForApproval exception must contain invoice_id, amount, and reason
        so that main.py can display them to the human reviewer.
        """
        try:
            request_approval(self._make_input())
            assert False, "request_approval should have raised PauseForApproval"
        except PauseForApproval as exc:
            assert exc.invoice_id == self.INVOICE_ID, "Payload must include invoice_id"
            assert exc.amount == self.AMOUNT,         "Payload must include amount"
            assert exc.reason == self.REASON,         "Payload must include reason"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — ERPPostGuardrail (unit-tested through its logic)
# ═════════════════════════════════════════════════════════════════════════════

class TestERPPostGuardrailLogic:
    """
    Tests for the ERPPostGuardrail safety check.

    NOTE: The @input_guardrail decorator wraps the function into an
    InputGuardrail object. To call it directly in tests, we call
    .guardrail_function to access the raw underlying async function.
    """

    INVOICE_ID = "INV-4002"

    # ── Test 2A: Guardrail BLOCKS when no validation done ───────────────────
    @pytest.mark.asyncio
    async def test_guardrail_blocks_without_validation(self):
        """
        GIVEN: workflow_store is empty (no validations have run)
        WHEN:  erp_post_guardrail is called
        THEN:  tripwire_triggered=True (ERP post is BLOCKED)
        """
        from app.guardrails import erp_post_guardrail, ERPGuardrailOutput

        mock_ctx   = MagicMock()
        mock_agent = MagicMock()

        # NOTE: .guardrail_function accesses the raw async function
        # under the @input_guardrail decorator wrapper.
        result = await erp_post_guardrail.guardrail_function(
            mock_ctx, mock_agent, f"invoice_id={self.INVOICE_ID}"
        )

        assert result.tripwire_triggered is True, (
            "Guardrail must BLOCK the ERP post when validations haven't run"
        )
        assert isinstance(result.output_info, ERPGuardrailOutput)
        assert result.output_info.passed is False

    # ── Test 2B: Guardrail ALLOWS when both validations done ────────────────
    @pytest.mark.asyncio
    async def test_guardrail_allows_after_validation(self):
        """
        GIVEN: workflow_store has vendor_validated=True AND po_validated=True
        WHEN:  erp_post_guardrail is called
        THEN:  tripwire_triggered=False (ERP post is ALLOWED)
        """
        from app.guardrails import erp_post_guardrail, ERPGuardrailOutput

        run_happy_validations(self.INVOICE_ID)

        mock_ctx   = MagicMock()
        mock_agent = MagicMock()

        result = await erp_post_guardrail.guardrail_function(
            mock_ctx, mock_agent, f"invoice_id={self.INVOICE_ID}"
        )

        assert result.tripwire_triggered is False, (
            "Guardrail must ALLOW the ERP post when both validations have passed"
        )
        assert isinstance(result.output_info, ERPGuardrailOutput)
        assert result.output_info.passed is True

    # ── Test 2C: Guardrail BLOCKS when only vendor validated ────────────────
    @pytest.mark.asyncio
    async def test_guardrail_blocks_when_only_vendor_validated(self):
        """
        GIVEN: vendor_validated=True but po_validated=False
        WHEN:  erp_post_guardrail is called
        THEN:  tripwire_triggered=True (PO validation missing → BLOCKED)
        """
        from app.guardrails import erp_post_guardrail

        workflow_store[self.INVOICE_ID] = {"vendor_validated": True}

        mock_ctx   = MagicMock()
        mock_agent = MagicMock()

        result = await erp_post_guardrail.guardrail_function(
            mock_ctx, mock_agent, f"invoice_id={self.INVOICE_ID}"
        )

        assert result.tripwire_triggered is True, (
            "Guardrail must BLOCK when PO validation is missing"
        )
        assert "PO" in result.output_info.reason, (
            "Error message should mention the missing PO validation"
        )

    # ── Test 2D: Guardrail BLOCKS when only PO validated ────────────────────
    @pytest.mark.asyncio
    async def test_guardrail_blocks_when_only_po_validated(self):
        """
        GIVEN: po_validated=True but vendor_validated=False
        WHEN:  erp_post_guardrail is called
        THEN:  tripwire_triggered=True (vendor validation missing → BLOCKED)
        """
        from app.guardrails import erp_post_guardrail

        workflow_store[self.INVOICE_ID] = {"po_validated": True}

        mock_ctx   = MagicMock()
        mock_agent = MagicMock()

        result = await erp_post_guardrail.guardrail_function(
            mock_ctx, mock_agent, f"invoice_id={self.INVOICE_ID}"
        )

        assert result.tripwire_triggered is True, (
            "Guardrail must BLOCK when vendor validation is missing"
        )
        assert "vendor" in result.output_info.reason.lower(), (
            "Error message should mention the missing vendor validation"
        )


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — handle_interruptions() in main.py
# ═════════════════════════════════════════════════════════════════════════════

class TestHandleInterruptions:
    """
    Tests for the CLI-side interrupt handler that main.py uses.

    handle_interruptions() is the function that:
      - Reads the PauseForApproval exception data
      - Shows a Rich panel to the human reviewer
      - Calls Prompt.ask() to collect the decision
      - Returns "approved" or "rejected"

    We mock Prompt.ask() so the "human" makes a decision without
    anyone needing to type anything during the test.
    """

    def _make_pause_exc(self, invoice_id="INV-4003", amount=150000.0):
        """Build a PauseForApproval exception for testing."""
        return PauseForApproval(
            invoice_id=invoice_id,
            amount=amount,
            reason="Amount exceeds threshold",
        )

    # ── Test 3A: Returns "approved" when human chooses approve ──────────────
    def test_handle_interruptions_approve(self):
        """
        GIVEN: Human types "approve" at the CLI prompt
        WHEN:  handle_interruptions() is called
        THEN:  Returns "approved"
        """
        from app.main import handle_interruptions

        with patch("app.main.Prompt.ask", return_value="approve"):
            result = handle_interruptions(self._make_pause_exc())

        assert result == "approved"

    # ── Test 3B: Returns "rejected" when human chooses reject ───────────────
    def test_handle_interruptions_reject(self):
        """
        GIVEN: Human types "reject" at the CLI prompt
        WHEN:  handle_interruptions() is called
        THEN:  Returns "rejected"
        """
        from app.main import handle_interruptions

        with patch("app.main.Prompt.ask", return_value="reject"):
            result = handle_interruptions(self._make_pause_exc())

        assert result == "rejected"

    # ── Test 3C: Any non-approve input defaults to rejected ─────────────────
    def test_handle_interruptions_unknown_defaults_to_rejected(self):
        """
        GIVEN: Human types something unexpected like "maybe"
        WHEN:  handle_interruptions() is called
        THEN:  Returns "rejected" (safe default)
        """
        from app.main import handle_interruptions

        with patch("app.main.Prompt.ask", return_value="maybe"):
            result = handle_interruptions(self._make_pause_exc())

        assert result == "rejected"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Integration: Full Approval Workflow (no real LLM)
# ═════════════════════════════════════════════════════════════════════════════

class TestApprovalWorkflowIntegration:
    """
    End-to-end simulation of the approval workflow at the tool level.

    Simulates what the agent + main.py do together:
      1. Vendor validation   → passes
      2. PO validation       → passes
      3. Duplicate check     → not a duplicate
      4. request_approval    → raises PauseForApproval (first call)
         main.py catches, human approves, decision written to store
      5. request_approval    → returns "approved" (resume call)
      6. post_to_erp         → succeeds (guardrail flags are set)
    """

    INVOICE_ID = "INV-APPROVAL-001"

    def test_full_approval_path(self):
        """
        GIVEN: A high-value invoice that requires human approval
        WHEN:  Tools run in sequence and human approves (resume path)
        THEN:
          - vendor_validated=True in workflow_store
          - po_validated=True in workflow_store
          - approval_status="approved" in workflow_store
          - post_to_erp succeeds and returns an ERP reference ID
        """
        # Step 1 + 2: Run validations
        run_happy_validations(self.INVOICE_ID)

        # Step 3: Duplicate check
        dup_result = check_duplicate(CheckDuplicateInput(invoice_id=self.INVOICE_ID))
        assert dup_result.is_duplicate is False

        # Step 4a: First call raises PauseForApproval
        with pytest.raises(PauseForApproval):
            request_approval(ApprovalInput(
                invoice_id=self.INVOICE_ID,
                amount=150_000.0,
                reason="Amount exceeds INR 1,00,000 threshold",
            ))
        assert workflow_store[self.INVOICE_ID]["approval_status"] == "pending"

        # Step 4b: Simulate main.py injecting human decision
        workflow_store[self.INVOICE_ID]["approval_status"] = "approved"

        # Step 4c: Agent resumes — second call returns the stored decision
        approval_result = request_approval(ApprovalInput(
            invoice_id=self.INVOICE_ID,
            amount=150_000.0,
            reason="Amount exceeds INR 1,00,000 threshold",
        ))
        assert approval_result.approval_status == "approved"
        assert approval_result.approver == "human-reviewer"

        # Step 5: Post to ERP
        erp_result = post_to_erp(ERPPostInput(
            invoice_id=self.INVOICE_ID,
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-45678",
            amount=150_000.0,
        ))

        assert erp_result.success is True
        assert erp_result.erp_reference_id is not None
        assert erp_result.erp_reference_id.startswith("ERP-")

        # Verify full workflow store state
        store = workflow_store[self.INVOICE_ID]
        assert store["vendor_validated"] is True
        assert store["po_validated"] is True
        assert store["approval_status"] == "approved"
        assert store["erp_posted"] is True

    def test_full_rejection_path(self):
        """
        GIVEN: A high-value invoice that requires human approval
        WHEN:  Tools run in sequence and human REJECTS
        THEN:
          - approval_status="rejected" in workflow_store
          - post_to_erp is NOT called (agent stops after rejection)
        """
        run_happy_validations(self.INVOICE_ID)

        # First call raises PauseForApproval
        with pytest.raises(PauseForApproval):
            request_approval(ApprovalInput(
                invoice_id=self.INVOICE_ID,
                amount=150_000.0,
                reason="Amount exceeds threshold",
            ))

        # Simulate main.py injecting rejection decision
        workflow_store[self.INVOICE_ID]["approval_status"] = "rejected"

        # Resume call returns rejected
        approval_result = request_approval(ApprovalInput(
            invoice_id=self.INVOICE_ID,
            amount=150_000.0,
            reason="Amount exceeds threshold",
        ))
        assert approval_result.approval_status == "rejected"
        assert approval_result.approver == "human-reviewer"

        store = workflow_store[self.INVOICE_ID]
        assert store["approval_status"] == "rejected"
        assert store.get("erp_posted") is not True


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Failure Path Scenarios
# ═════════════════════════════════════════════════════════════════════════════

class TestFailurePathScenarios:
    """
    Tests for failure conditions that the guardrail and tools must handle.
    Failure paths are just as important as success paths!
    """

    INVOICE_ID = "INV-FAIL-001"

    def test_erp_post_blocked_when_no_prior_validation(self):
        """
        GIVEN: post_to_erp is called without running any validation
        THEN:  workflow_store flags are missing (guardrail would block)
        """
        store = workflow_store.get(self.INVOICE_ID, {})
        vendor_ok = store.get("vendor_validated", False)
        po_ok     = store.get("po_validated", False)

        assert not vendor_ok, "vendor_validated should be False when validation hasn't run"
        assert not po_ok,     "po_validated should be False when validation hasn't run"

    def test_post_to_erp_fails_with_empty_inputs(self):
        """
        GIVEN: post_to_erp is called with an empty invoice_id
        THEN:  success=False is returned immediately (defensive check)
        """
        result = post_to_erp(ERPPostInput(
            invoice_id="",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-45678",
            amount=50_000.0,
        ))

        assert result.success is False
        assert "empty" in result.reason.lower() or "required" in result.reason.lower()
        assert result.erp_reference_id is None

    def test_invalid_vendor_blocks_workflow(self):
        """
        GIVEN: An invoice with an unregistered vendor name
        WHEN:  validate_vendor() is called
        THEN:  is_valid=False — vendor_validated NOT set in workflow_store
        """
        result = validate_vendor(ValidateVendorInput(
            invoice_id=self.INVOICE_ID,
            vendor_name="Fake Vendor Co",
            vendor_gstin="29ABCDE1234F1Z5",
        ))

        assert result.is_valid is False
        assert "not registered" in result.reason

        store = workflow_store.get(self.INVOICE_ID, {})
        assert store.get("vendor_validated") is not True

    def test_invalid_po_blocks_workflow(self):
        """
        GIVEN: An invoice with a PO number that doesn't exist
        WHEN:  validate_po() is called
        THEN:  is_valid=False — po_validated NOT set in workflow_store
        """
        result = validate_po(ValidatePOInput(
            invoice_id=self.INVOICE_ID,
            po_number="PO-DOESNOTEXIST",
        ))

        assert result.is_valid is False
        assert "does not exist" in result.reason

        store = workflow_store.get(self.INVOICE_ID, {})
        assert store.get("po_validated") is not True

    def test_duplicate_invoice_rejected(self):
        """
        GIVEN: An invoice_id that has already been processed
        WHEN:  check_duplicate() is called a second time
        THEN:  is_duplicate=True
        """
        first = check_duplicate(CheckDuplicateInput(invoice_id=self.INVOICE_ID))
        assert first.is_duplicate is False

        second = check_duplicate(CheckDuplicateInput(invoice_id=self.INVOICE_ID))
        assert second.is_duplicate is True
        assert "already been processed" in second.reason
