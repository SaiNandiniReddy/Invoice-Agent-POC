"""
app/guardrails.py — Tool guardrail logic for the Invoice Workflow Agent POC.

Day 1 Status: SKELETON — class structure defined.
              Full validation logic is implemented on Day 4.

Guardrails sit between the agent and the tool — they intercept tool calls
and can block them if preconditions are not met. This is an OpenAI Agents
SDK feature that provides a safety layer independent of the agent's reasoning.
"""

# TODO (Day 4): Implement using the agents SDK guardrail pattern
# from agents import GuardrailFunctionOutput, input_guardrail
# from pydantic import BaseModel

# class ERPGuardrailOutput(BaseModel):
#     passed: bool
#     reason: str

# @input_guardrail
# async def erp_post_guardrail(ctx, agent, input) -> GuardrailFunctionOutput:
#     """
#     Blocks post_to_erp from being called if any required field is missing.
#
#     Required fields: invoice_id, vendor_gstin, po_number, amount
#
#     This demonstrates the SDK's guardrail capability for safety validation
#     before a tool call executes its side effects.
#     """
#     required = ["invoice_id", "vendor_gstin", "po_number", "amount"]
#     missing = [f for f in required if not input.get(f)]
#     if missing:
#         return GuardrailFunctionOutput(
#             output_info=ERPGuardrailOutput(passed=False, reason=f"Missing fields: {missing}"),
#             tripwire_triggered=True,
#         )
#     return GuardrailFunctionOutput(
#         output_info=ERPGuardrailOutput(passed=True, reason="All required fields present"),
#         tripwire_triggered=False,
#     )
