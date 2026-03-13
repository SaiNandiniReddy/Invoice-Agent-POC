"""
tests/test_phase3.py — Unit tests for Phase 3: Agent Core & Structured Outputs.

IMPORTANT: These tests do NOT call the OpenAI API.
           They run fully offline using mocks and logic-only assertions.

What is tested:
  Section 1 — TestAgentConfiguration     : agent has correct tools, output_type, model
  Section 2 — TestBuildInvoiceMessage    : invoice → string message formatting
  Section 3 — TestApplyDecisionToState   : NextActionDecision → WorkflowState mapping
  Section 4 — TestSDKToolWrappers        : wrappers correctly call underlying Pydantic tools
  Section 5 — TestMockedRunnerIntegration: full pipeline simulation (no API)

How to run:
    pytest tests/test_phase3.py -v
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import app.config as config
import app.tools as tools_module


# ── Reset shared state before every test ─────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_shared_state():
    config.PROCESSED_INVOICES.clear()
    tools_module.workflow_store.clear()
    yield
    config.PROCESSED_INVOICES.clear()
    tools_module.workflow_store.clear()


# ── Shared helper: build a valid Invoice ─────────────────────────────────────
def make_invoice(
    invoice_id="INV-TEST-001",
    vendor_name="Tech Supplies Ltd",
    vendor_gstin="29ABCDE1234F1Z5",
    po_number="PO-12345",
    invoice_amount=45000.0,
):
    from app.state import Invoice
    return Invoice(
        invoice_id=invoice_id,
        vendor_name=vendor_name,
        vendor_gstin=vendor_gstin,
        po_number=po_number,
        invoice_amount=invoice_amount,
        invoice_date="2026-03-08",
    )


def make_decision(next_action, reason="Test reason", confidence=0.95):
    from app.state import NextActionDecision
    return NextActionDecision(
        next_action=next_action,
        reason=reason,
        confidence=confidence,
    )


# =============================================================================
# Section 1: Agent Configuration
# =============================================================================

class TestAgentConfiguration:
    """Verify the invoice_agent object is set up correctly."""

    def test_agent_is_created(self):
        """invoice_agent should be importable and not None."""
        from app.agent import invoice_agent
        assert invoice_agent is not None

    def test_agent_has_correct_name(self):
        from app.agent import invoice_agent
        assert invoice_agent.name == "invoice-workflow-agent"

    def test_agent_output_type_is_next_action_decision(self):
        """Structured output must be NextActionDecision."""
        from app.agent import invoice_agent
        from app.state import NextActionDecision
        assert invoice_agent.output_type is NextActionDecision

    def test_agent_has_exactly_six_tools(self):
        """All 6 tool wrappers must be registered."""
        from app.agent import invoice_agent
        assert len(invoice_agent.tools) == 6

    def test_agent_instructions_not_empty(self):
        from app.agent import invoice_agent
        assert invoice_agent.instructions
        assert len(invoice_agent.instructions) > 50

    def test_agent_model_is_gpt_based(self):
        from app.agent import invoice_agent
        assert "gpt" in str(invoice_agent.model).lower()

    def test_agent_instructions_mention_validate_vendor(self):
        """Instructions must mention the first step so the agent knows what to do."""
        from app.agent import invoice_agent
        assert "validate_vendor" in invoice_agent.instructions.lower()

    def test_agent_instructions_mention_rejection(self):
        from app.agent import invoice_agent
        assert "rejected" in invoice_agent.instructions.lower()


# =============================================================================
# Section 2: build_invoice_message
# =============================================================================

class TestBuildInvoiceMessage:
    """Verify that build_invoice_message formats invoice data correctly."""

    def test_message_contains_invoice_id(self):
        from app.main import build_invoice_message
        msg = build_invoice_message(make_invoice())
        assert "INV-TEST-001" in msg

    def test_message_contains_vendor_name(self):
        from app.main import build_invoice_message
        msg = build_invoice_message(make_invoice())
        assert "Tech Supplies Ltd" in msg

    def test_message_contains_gstin(self):
        from app.main import build_invoice_message
        msg = build_invoice_message(make_invoice())
        assert "29ABCDE1234F1Z5" in msg

    def test_message_contains_po_number(self):
        from app.main import build_invoice_message
        msg = build_invoice_message(make_invoice())
        assert "PO-12345" in msg

    def test_message_contains_formatted_amount(self):
        from app.main import build_invoice_message
        msg = build_invoice_message(make_invoice(invoice_amount=45000.0))
        assert "45,000.00" in msg

    def test_message_instructs_tool_calls_with_invoice_id(self):
        """Agent must use the correct invoice_id for all tool calls."""
        from app.main import build_invoice_message
        msg = build_invoice_message(make_invoice())
        assert 'invoice_id="INV-TEST-001"' in msg


# =============================================================================
# Section 3: apply_decision_to_state
# =============================================================================

class TestApplyDecisionToState:
    """Verify that WorkflowState is updated correctly for every decision type."""

    def test_complete_sets_completed_status(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("complete"), make_invoice())
        assert state.status == WorkflowStatus.COMPLETED

    def test_complete_sets_completed_at_timestamp(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("complete"), make_invoice())
        assert state.completed_at is not None

    def test_complete_reads_erp_ref_from_store(self):
        """If the agent posted to ERP, the reference ID should be in state."""
        from app.main import apply_decision_to_state
        from app.state import WorkflowState
        # Simulate ERP post already happened (workflow_store populated)
        tools_module.workflow_store["INV-TEST-001"] = {"erp_reference_id": "ERP-ABCD1234"}
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("complete"), make_invoice())
        assert state.erp_reference_id == "ERP-ABCD1234"

    def test_rejected_sets_rejected_status(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(
            state, make_decision("rejected", reason="Vendor not registered"), make_invoice()
        )
        assert state.status == WorkflowStatus.REJECTED

    def test_rejected_stores_rejection_reason(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(
            state, make_decision("rejected", reason="Vendor not registered"), make_invoice()
        )
        assert state.rejection_reason == "Vendor not registered"

    def test_manual_review_sets_correct_status(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("manual_review"), make_invoice())
        assert state.status == WorkflowStatus.MANUAL_REVIEW

    def test_request_approval_sets_awaiting_approval(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("request_approval"), make_invoice())
        assert state.status == WorkflowStatus.AWAITING_APPROVAL

    def test_request_approval_sets_pending_approval_status(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, ApprovalStatus
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("request_approval"), make_invoice())
        assert state.approval_status == ApprovalStatus.PENDING

    def test_every_decision_adds_one_action_record(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState
        for action in ["complete", "rejected", "manual_review", "request_approval"]:
            config.PROCESSED_INVOICES.clear()
            tools_module.workflow_store.clear()
            state = WorkflowState(invoice_id="INV-TEST-001")
            apply_decision_to_state(state, make_decision(action), make_invoice())
            assert len(state.action_history) == 1, f"Expected 1 action for '{action}'"

    def test_action_record_tool_name_is_agent_decision(self):
        from app.main import apply_decision_to_state
        from app.state import WorkflowState
        state = WorkflowState(invoice_id="INV-TEST-001")
        apply_decision_to_state(state, make_decision("complete"), make_invoice())
        assert state.action_history[0].tool_name == "agent_decision"


# =============================================================================
# Section 4: SDK Tool Wrappers
# =============================================================================

class TestSDKToolWrappers:
    """Verify the flat wrapper functions delegate correctly to Pydantic tools."""

    def test_tool_validate_vendor_valid_vendor(self):
        from app.agent import tool_validate_vendor
        result = tool_validate_vendor(
            invoice_id="INV-001",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        )
        assert "is_valid=True" in result

    def test_tool_validate_vendor_unknown_vendor(self):
        from app.agent import tool_validate_vendor
        result = tool_validate_vendor(
            invoice_id="INV-002",
            vendor_name="Fake Vendor Corp",
            vendor_gstin="99ZZZZZ9999Z9Z9",
        )
        assert "is_valid=False" in result

    def test_tool_validate_vendor_gstin_mismatch(self):
        from app.agent import tool_validate_vendor
        result = tool_validate_vendor(
            invoice_id="INV-003",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="00XXXXX0000X0X0",
        )
        assert "is_valid=False" in result
        assert "mismatch" in result.lower()

    def test_tool_validate_po_valid(self):
        from app.agent import tool_validate_po
        result = tool_validate_po(invoice_id="INV-001", po_number="PO-12345")
        assert "is_valid=True" in result
        assert "50000" in result

    def test_tool_validate_po_invalid(self):
        from app.agent import tool_validate_po
        result = tool_validate_po(invoice_id="INV-001", po_number="PO-00000")
        assert "is_valid=False" in result
        assert "po_amount=None" in result

    def test_tool_check_duplicate_first_call_not_duplicate(self):
        from app.agent import tool_check_duplicate
        result = tool_check_duplicate(invoice_id="INV-NEW-001")
        assert "is_duplicate=False" in result

    def test_tool_check_duplicate_second_call_is_duplicate(self):
        from app.agent import tool_check_duplicate
        tool_check_duplicate(invoice_id="INV-DUP-001")  # first call registers it
        result = tool_check_duplicate(invoice_id="INV-DUP-001")
        assert "is_duplicate=True" in result

    def test_tool_request_approval_always_pending(self):
        """
        In Phase 4, tool_request_approval raises PauseForApproval on the
        first call. After the raise, workflow_store[invoice_id]['approval_status']
        is already set to 'pending'.
        """
        from app.agent import tool_request_approval
        from app.tools import PauseForApproval
        with pytest.raises(PauseForApproval):
            tool_request_approval(
                invoice_id="INV-001", amount=125000.0, reason="High value invoice"
            )
        # Side-effect: approval_status stored before exception propagated
        assert tools_module.workflow_store["INV-001"]["approval_status"] == "pending"

    def test_tool_post_to_erp_success(self):
        from app.agent import tool_post_to_erp
        result = tool_post_to_erp(
            invoice_id="INV-001",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=45000.0,
        )
        assert "success=True" in result
        assert "ERP-" in result

    def test_tool_post_to_erp_empty_invoice_id_fails(self):
        from app.agent import tool_post_to_erp
        result = tool_post_to_erp(
            invoice_id="",
            vendor_gstin="29ABCDE1234F1Z5",
            po_number="PO-12345",
            amount=45000.0,
        )
        assert "success=False" in result

    def test_tool_get_invoice_summary_unknown_returns_pending(self):
        from app.agent import tool_get_invoice_summary
        result = tool_get_invoice_summary(invoice_id="INV-UNKNOWN")
        assert "status=pending" in result
        assert "tools_called=[]" in result

    def test_tool_get_invoice_summary_after_vendor_validation(self):
        from app.agent import tool_validate_vendor, tool_get_invoice_summary
        tool_validate_vendor(
            invoice_id="INV-SUM-001",
            vendor_name="Tech Supplies Ltd",
            vendor_gstin="29ABCDE1234F1Z5",
        )
        result = tool_get_invoice_summary(invoice_id="INV-SUM-001")
        assert "validate_vendor" in result
        assert "status=in_progress" in result


# =============================================================================
# Section 5: Mocked Runner Integration
# =============================================================================

class TestMockedRunnerIntegration:
    """
    Simulate complete agent runs — no API calls needed.

    These tests mock the Runner so we can test the full main.py logic
    end-to-end: invoice → message → (mocked) decision → state → saved JSON.
    """

    def test_happy_path_state_is_completed(self):
        """Simulate agent saying 'complete' → state becomes COMPLETED."""
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus
        inv = make_invoice()
        state = WorkflowState(invoice_id=inv.invoice_id)
        decision = make_decision("complete", reason="All steps passed. ERP posted.")
        apply_decision_to_state(state, decision, inv)
        assert state.status == WorkflowStatus.COMPLETED

    def test_failure_path_unknown_vendor_is_rejected(self):
        """Simulate agent saying 'rejected' for unknown vendor."""
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus
        inv = make_invoice(vendor_name="Fake Vendor Corp", vendor_gstin="99ZZZZZ9999Z9Z9")
        state = WorkflowState(invoice_id=inv.invoice_id)
        decision = make_decision("rejected", reason="Vendor 'Fake Vendor Corp' is not registered.")
        apply_decision_to_state(state, decision, inv)
        assert state.status == WorkflowStatus.REJECTED
        assert "not registered" in state.rejection_reason

    def test_approval_path_high_value_awaits_approval(self):
        """Simulate agent routing INR 1,25,000 invoice to approval."""
        from app.main import apply_decision_to_state
        from app.state import WorkflowState, WorkflowStatus, ApprovalStatus
        inv = make_invoice(invoice_amount=125000.0, po_number="PO-45678")
        state = WorkflowState(invoice_id=inv.invoice_id)
        decision = make_decision("request_approval", reason="Amount > INR 1,00,000.")
        apply_decision_to_state(state, decision, inv)
        assert state.status == WorkflowStatus.AWAITING_APPROVAL
        assert state.approval_status == ApprovalStatus.PENDING

    def test_result_json_is_valid_and_saved(self, tmp_path, monkeypatch):
        """Verify save_result() writes a valid JSON file with correct fields."""
        from app.main import save_result
        from app.state import WorkflowState
        monkeypatch.setattr("app.main.OUTPUT_DIR", str(tmp_path))
        state = WorkflowState(invoice_id="INV-SAVE-001")
        state.complete(erp_reference_id="ERP-TESTREF1")
        out_path = save_result(state)
        assert out_path.exists(), "Result JSON file was not created"
        data = json.loads(out_path.read_text())
        assert data["invoice_id"] == "INV-SAVE-001"
        assert data["status"] == "completed"
        assert data["erp_reference_id"] == "ERP-TESTREF1"

    def test_full_happy_path_tool_pipeline(self):
        """
        Run all 5 tool wrappers in sequence (happy path) and verify final
        summary shows status=completed with all tools recorded.
        This tests the tool layer end-to-end without an LLM.
        """
        from app.agent import (
            tool_validate_vendor, tool_validate_po, tool_check_duplicate,
            tool_post_to_erp, tool_get_invoice_summary,
        )
        INV_ID = "INV-FULL-HAPPY"

        v = tool_validate_vendor(INV_ID, "Tech Supplies Ltd", "29ABCDE1234F1Z5")
        assert "is_valid=True" in v

        p = tool_validate_po(INV_ID, "PO-12345")
        assert "is_valid=True" in p

        d = tool_check_duplicate(INV_ID)
        assert "is_duplicate=False" in d

        e = tool_post_to_erp(INV_ID, "29ABCDE1234F1Z5", "PO-12345", 45000.0)
        assert "success=True" in e
        assert "ERP-" in e

        s = tool_get_invoice_summary(INV_ID)
        assert "status=completed" in s
        assert "validate_vendor" in s
        assert "post_to_erp" in s
