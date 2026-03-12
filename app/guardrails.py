"""
app/guardrails.py — Tool guardrail logic for the Invoice Workflow Agent POC.

Phase 4 Status: COMPLETE — ERPPostGuardrail fully implemented.

What is a Guardrail?
─────────────────────
A guardrail is a safety check that runs BEFORE a tool executes.
Think of it like a security guard at a door:
  • The guard checks your ID *before* letting you in.
  • If you don't meet the requirements, you're turned away immediately.
  • The actual tool code never even runs.

The OpenAI Agents SDK supports TWO kinds of guardrails:
  1. input_guardrail   — fires before the tool call (used here)
  2. output_guardrail  — fires after the tool returns

ERPPostGuardrail
─────────────────
Blocks post_to_erp from running unless the invoice has already passed
both vendor validation AND PO validation.

This prevents a scenario where the LLM skips validation steps and
tries to post directly to the ERP — which would be a serious data
integrity problem in production.

How it works:
  1. Reads the workflow_store (shared in-memory dict in tools.py)
  2. Checks that vendor_validated=True AND po_validated=True
  3. If either flag is missing → tripwire_triggered=True (tool is BLOCKED)
  4. If both flags are present → tool is allowed to proceed
"""

from __future__ import annotations

from pydantic import BaseModel

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    TResponseInputItem,
    input_guardrail,
)

from app.tools import workflow_store


# ─────────────────────────────────────────────────────────────────────────────
# Output Model — what the guardrail "reports back" to the SDK
# ─────────────────────────────────────────────────────────────────────────────

class ERPGuardrailOutput(BaseModel):
    """
    Structured result that the guardrail returns to the SDK.

    Fields
    ──────
    passed : bool — True if the guardrail check passed (tool may proceed).
    reason : str  — Human-readable explanation of why it passed or failed.
    """
    passed: bool
    reason: str


# ─────────────────────────────────────────────────────────────────────────────
# The Guardrail Function
# ─────────────────────────────────────────────────────────────────────────────

@input_guardrail
async def erp_post_guardrail(
    ctx: RunContextWrapper,
    agent: Agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """
    Input guardrail that blocks post_to_erp unless validation steps
    have already been completed for the invoice.

    When the agent calls tool_post_to_erp, the SDK runs this guardrail
    function FIRST. The guardrail:
      1. Extracts the invoice_id from the conversation context.
      2. Looks up the workflow_store to check validation flags.
      3. Returns tripwire_triggered=True to BLOCK the call if flags are missing.

    ┌─────────────────────────────────────────────────────────┐
    │  tripwire_triggered=True  →  tool is BLOCKED            │
    │  tripwire_triggered=False →  tool is ALLOWED to run     │
    └─────────────────────────────────────────────────────────┘

    Parameters
    ──────────
    ctx   : RunContextWrapper — provides access to the run context.
    agent : Agent             — the agent making the tool call.
    input : str or list       — the raw input passed by the agent.

    Returns
    ───────
    GuardrailFunctionOutput with:
        output_info        : ERPGuardrailOutput (passed flag + reason)
        tripwire_triggered : True = block, False = allow
    """
    # ── Extract invoice_id from context ──────────────────────────────────────
    # The agent passes the invoice_id as part of its tool call arguments.
    # We search all known invoices in the workflow_store to find which one
    # has been validated. For a POC with single-invoice processing, we
    # check if ANY invoice in the store has both flags set.
    #
    # In production, you would extract invoice_id from the structured
    # tool-call arguments in `input`.

    invoice_id: str | None = None

    # Try to extract invoice_id from the input string/list
    raw = input if isinstance(input, str) else str(input)
    for iid in workflow_store:
        if iid in raw:
            invoice_id = iid
            break

    # Fallback: check any invoice in store (single-invoice POC)
    if invoice_id is None and workflow_store:
        invoice_id = next(iter(workflow_store))

    if invoice_id is None:
        # No invoice in store at all — block the call
        return GuardrailFunctionOutput(
            output_info=ERPGuardrailOutput(
                passed=False,
                reason="No invoice found in workflow store. Vendor and PO validation must be run first.",
            ),
            tripwire_triggered=True,
        )

    store = workflow_store.get(invoice_id, {})
    vendor_ok = store.get("vendor_validated", False)
    po_ok = store.get("po_validated", False)

    # ── Decision ──────────────────────────────────────────────────────────────
    if not vendor_ok and not po_ok:
        return GuardrailFunctionOutput(
            output_info=ERPGuardrailOutput(
                passed=False,
                reason=(
                    f"ERP post BLOCKED for invoice '{invoice_id}': "
                    "vendor validation AND PO validation have not been completed."
                ),
            ),
            tripwire_triggered=True,
        )

    if not vendor_ok:
        return GuardrailFunctionOutput(
            output_info=ERPGuardrailOutput(
                passed=False,
                reason=(
                    f"ERP post BLOCKED for invoice '{invoice_id}': "
                    "vendor validation has not been completed."
                ),
            ),
            tripwire_triggered=True,
        )

    if not po_ok:
        return GuardrailFunctionOutput(
            output_info=ERPGuardrailOutput(
                passed=False,
                reason=(
                    f"ERP post BLOCKED for invoice '{invoice_id}': "
                    "PO validation has not been completed."
                ),
            ),
            tripwire_triggered=True,
        )

    # All checks passed — allow the ERP post to proceed
    return GuardrailFunctionOutput(
        output_info=ERPGuardrailOutput(
            passed=True,
            reason=(
                f"ERP post ALLOWED for invoice '{invoice_id}': "
                "vendor validation and PO validation both confirmed."
            ),
        ),
        tripwire_triggered=False,
    )
