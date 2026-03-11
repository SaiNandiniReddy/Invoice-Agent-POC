"""
app/main.py — CLI Entry Point with Agent Decision Loop (Phase 3).

Phase 3 adds:
  - build_invoice_message()    : formats invoice data as agent input
  - apply_decision_to_state()  : maps NextActionDecision → WorkflowState
  - print_workflow_result()    : colour-coded terminal summary
  - run() command updated      : calls Runner and drives the workflow

Usage:
    python -m app.main --invoice sample_data/invoice_happy.json
    python -m app.main --invoice sample_data/invoice_approval.json
    python -m app.main --invoice sample_data/invoice_failure.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.config import OUTPUT_DIR, OPENAI_API_KEY
from app.state import (
    ApprovalStatus,
    Invoice,
    NextActionDecision,
    WorkflowState,
    WorkflowStatus,
)

app = typer.Typer(help="Invoice Workflow Agent POC — CLI")
console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Load Invoice
# ─────────────────────────────────────────────────────────────────────────────

def load_invoice(path: Path) -> Invoice:
    """Load and validate an invoice JSON file into an Invoice Pydantic model."""
    if not path.exists():
        console.print(f"[red]ERROR:[/] Invoice file not found: {path}")
        raise typer.Exit(code=1)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Invoice(**raw)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Build Agent Input Message
# ─────────────────────────────────────────────────────────────────────────────

def build_invoice_message(invoice: Invoice) -> str:
    """
    Format the invoice fields as a plain-text message for the agent.

    The agent receives this string as its input and uses the structured
    data to decide which tools to call in what order.
    """
    items_text = "\n".join(
        f"  - {item.description}: qty={item.quantity}, "
        f"unit_price=INR {item.unit_price:,.2f}, total=INR {item.total_price:,.2f}"
        for item in invoice.line_items
    ) or "  (no line items)"

    return (
        f"Process this invoice:\n\n"
        f"Invoice ID:     {invoice.invoice_id}\n"
        f"Vendor Name:    {invoice.vendor_name}\n"
        f"Vendor GSTIN:   {invoice.vendor_gstin}\n"
        f"PO Number:      {invoice.po_number}\n"
        f"Invoice Amount: INR {invoice.invoice_amount:,.2f}\n"
        f"Invoice Date:   {invoice.invoice_date}\n"
        f"Currency:       {invoice.currency}\n"
        f"Line Items:\n{items_text}\n\n"
        f"Use invoice_id=\"{invoice.invoice_id}\" for all tool calls."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Print Invoice Table
# ─────────────────────────────────────────────────────────────────────────────

def print_invoice_summary(invoice: Invoice) -> None:
    """Pretty-print an invoice using a Rich table."""
    table = Table(
        title=f"📄 Invoice: {invoice.invoice_id}",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Vendor",       invoice.vendor_name)
    table.add_row("GSTIN",        invoice.vendor_gstin)
    table.add_row("PO Number",    invoice.po_number)
    table.add_row("Amount",       f"₹{invoice.invoice_amount:,.2f}")
    table.add_row("Currency",     invoice.currency)
    table.add_row("Invoice Date", invoice.invoice_date)
    table.add_row("Line Items",   str(len(invoice.line_items)))
    console.print(table)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Apply Agent Decision → WorkflowState
# ─────────────────────────────────────────────────────────────────────────────

def apply_decision_to_state(
    state: WorkflowState,
    decision: NextActionDecision,
    invoice: Invoice,
) -> None:
    """
    Translate the agent's NextActionDecision into WorkflowState changes.

    Maps:
      next_action="complete"          → COMPLETED   (reads ERP ref from workflow_store)
      next_action="rejected"          → REJECTED    (sets rejection_reason)
      next_action="manual_review"     → MANUAL_REVIEW
      next_action="request_approval"  → AWAITING_APPROVAL + PENDING approval
    """
    action = decision.next_action.lower()

    if action == "complete":
        from app.tools import workflow_store
        erp_ref = workflow_store.get(invoice.invoice_id, {}).get("erp_reference_id")
        state.complete(erp_reference_id=erp_ref)
        state.add_action(
            tool_name="agent_decision",
            input_data={"next_action": decision.next_action},
            output_data={"status": "completed", "erp_reference_id": erp_ref},
        )

    elif action == "rejected":
        state.reject(reason=decision.reason)
        state.add_action(
            tool_name="agent_decision",
            input_data={"next_action": decision.next_action},
            output_data={"status": "rejected", "reason": decision.reason},
        )

    elif action == "manual_review":
        state.escalate(reason=decision.reason)
        state.add_action(
            tool_name="agent_decision",
            input_data={"next_action": decision.next_action},
            output_data={"status": "manual_review", "reason": decision.reason},
        )

    elif action in ("request_approval", "pending"):
        state.status = WorkflowStatus.AWAITING_APPROVAL
        state.approval_status = ApprovalStatus.PENDING
        state.add_action(
            tool_name="agent_decision",
            input_data={"next_action": decision.next_action},
            output_data={"status": "awaiting_approval"},
        )

    else:
        # Unknown action — escalate to manual review for safety
        state.escalate(reason=f"Unknown agent decision: {decision.next_action}")


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Save Result
# ─────────────────────────────────────────────────────────────────────────────

def save_result(state: WorkflowState) -> Path:
    """Save the final workflow state JSON to the output directory."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = Path(OUTPUT_DIR) / f"{state.invoice_id}_result.json"
    out_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Print Workflow Result
# ─────────────────────────────────────────────────────────────────────────────

def print_workflow_result(state: WorkflowState, decision: NextActionDecision) -> None:
    """Print a colour-coded summary of the final workflow outcome."""
    color_map = {
        WorkflowStatus.COMPLETED:         "green",
        WorkflowStatus.REJECTED:          "red",
        WorkflowStatus.MANUAL_REVIEW:     "yellow",
        WorkflowStatus.AWAITING_APPROVAL: "magenta",
    }
    color = color_map.get(state.status, "blue")

    lines = [
        f"Status:     [{color}]{state.status.value.upper()}[/{color}]",
        f"Decision:   {decision.next_action}",
        f"Confidence: {decision.confidence:.0%}",
        f"Reason:     {decision.reason}",
    ]
    if state.erp_reference_id:
        lines.append(f"ERP Ref:    [bold green]{state.erp_reference_id}[/bold green]")
    if state.rejection_reason:
        lines.append(f"Rejected:   [red]{state.rejection_reason}[/red]")

    icon = "✅" if state.status == WorkflowStatus.COMPLETED else "⚠️ "
    console.print(Panel(
        "\n".join(lines),
        title=f"{icon} Workflow Result",
        border_style=color,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# CLI Command
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def run(
    invoice: Path = typer.Option(
        ...,
        "--invoice", "-i",
        help="Path to the invoice JSON file to process",
    ),
) -> None:
    """
    Process a single invoice through the workflow agent.

    Phase 3 behaviour:
      1. Loads and validates the JSON into an Invoice model
      2. Builds a plain-text message for the agent
      3. Runs the invoice_agent via Runner (handles tool calls internally)
      4. Maps the final NextActionDecision to WorkflowState
      5. Saves result to output/<invoice_id>_result.json
      6. Prints a colour-coded summary
    """
    console.rule("[bold blue]Invoice Workflow Agent POC — Phase 3[/]")

    # ── Guard: check API key ──────────────────────────────────────────────────
    if not OPENAI_API_KEY:
        console.print(
            "[red]ERROR:[/] OPENAI_API_KEY is not set.\n"
            "Create a [bold].env[/bold] file with: OPENAI_API_KEY=sk-..."
        )
        raise typer.Exit(code=1)

    # ── Step 1: Load & validate invoice ──────────────────────────────────────
    console.print(f"\n[yellow]Loading invoice:[/] {invoice}\n")
    inv = load_invoice(invoice)
    print_invoice_summary(inv)

    # ── Step 2: Initialise workflow state ─────────────────────────────────────
    state = WorkflowState(invoice_id=inv.invoice_id, status=WorkflowStatus.IN_PROGRESS)

    # ── Step 3: Run the agent ─────────────────────────────────────────────────
    console.print("\n[cyan]🤖 Running Invoice Agent ...[/]\n")
    message = build_invoice_message(inv)

    try:
        from agents import Runner
        from app.agent import invoice_agent
        result = asyncio.run(Runner.run(invoice_agent, message))
        decision: NextActionDecision = result.final_output

    except Exception as exc:
        console.print(f"[red]Agent error:[/] {exc}")
        state.escalate(reason=f"Agent error: {exc}")
        save_result(state)
        raise typer.Exit(code=1)

    # ── Step 4: Apply decision to state ───────────────────────────────────────
    apply_decision_to_state(state, decision, inv)

    # ── Step 5: Save & display ────────────────────────────────────────────────
    out_path = save_result(state)
    print_workflow_result(state, decision)
    console.print(f"\n[dim]Result saved to:[/] {out_path}\n")


if __name__ == "__main__":
    app()
