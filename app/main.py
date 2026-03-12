"""
app/main.py — CLI Entry Point with HITL + Execution Tracing.

Phase 4 (HITL):
  - handle_interruptions()  : shows approval panel, collects human decision
  - run_with_hitl()         : pause → human input → resume loop

Phase 5 (Tracing):
  - TraceCollector records every tool call with timestamps & duration
  - sdk_trace() wraps each run in an OpenAI platform span
  - Trace JSON is saved to output/<invoice_id>_trace.json after every run
  - Rich trace summary table is printed at the end of each CLI run

Usage:
    python -m app.main run --invoice sample_data/invoice_happy.json
    python -m app.main run --invoice sample_data/invoice_approval.json
    python -m app.main run --invoice sample_data/invoice_failure.json
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
from rich.prompt import Prompt
from rich.table import Table

import app.config as config
from app.config import OUTPUT_DIR, OPENAI_API_KEY
from app.state import (
    ApprovalStatus,
    Invoice,
    NextActionDecision,
    WorkflowState,
    WorkflowStatus,
)
from app.tracing import TraceCollector, sdk_trace, generate_trace_summary

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
# Phase 4 — Human-in-the-Loop: Handle Interruptions
# ─────────────────────────────────────────────────────────────────────────────

def handle_interruptions(pause_exc: "PauseForApproval") -> str:  # type: ignore[name-defined]
    """
    Display the approval request panel to the human reviewer and collect a decision.

    Called when a PauseForApproval exception is caught in run_with_hitl().
    This function:
      1. Reads invoice_id, amount, and reason from the PauseForApproval exception.
      2. Renders a clear Rich panel with all the invoice details.
      3. Prompts the reviewer to type "approve" or "reject".
      4. Returns "approved" or "rejected" — which main.py writes to workflow_store
         before re-running the agent.

    Parameters
    ──────────
    pause_exc : PauseForApproval — the exception raised by request_approval().

    Returns
    ───────
    str — "approved" or "rejected" (the human's decision).
    """
    # Extract details from the exception object
    invoice_id = getattr(pause_exc, "invoice_id", "UNKNOWN")
    amount     = getattr(pause_exc, "amount", 0.0)
    reason     = getattr(pause_exc, "reason", "No reason given")

    # ── Display the approval request panel ───────────────────────────────
    console.print(Panel(
        f"[bold yellow]⏸  AGENT PAUSED — HUMAN APPROVAL REQUIRED[/bold yellow]\n\n"
        f"[bold]Invoice ID:[/bold]  {invoice_id}\n"
        f"[bold]Amount:[/bold]      ₹{amount:,.2f} INR\n"
        f"[bold]Reason:[/bold]      {reason}\n\n"
        "[dim]The invoice amount exceeds the ₹1,00,000 approval threshold.\n"
        "A human reviewer must approve or reject before processing continues.[/dim]",
        title="🔔 APPROVAL REQUEST",
        border_style="yellow",
    ))

    # ── Collect the human decision ────────────────────────────────────────
    # Ask once — any answer that isn't "approve" is treated as "rejected" (safe default)
    raw = Prompt.ask(
        "\n[bold yellow]Your decision[/bold yellow]",
        choices=["approve", "reject"],
        default="reject",
    )
    return "approved" if raw.strip().lower() == "approve" else "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Human-in-the-Loop: Run Agent with Interrupt Support
# ─────────────────────────────────────────────────────────────────────────────

async def run_with_hitl(
    agent,
    message: str,
    collector: TraceCollector | None = None,
) -> NextActionDecision:
    """
    Run the invoice agent with HITL support and optional tracing.

    Phase 5 update: accepts an optional TraceCollector so every agent
    iteration is wrapped in an SDK trace span. The collector is populated
    externally (by the run() command) and saved after this function returns.

    HITL loop:
      1. Run agent — catch PauseForApproval if raised
      2. Show approval panel, collect human decision
      3. Write decision to workflow_store
      4. Re-run agent with updated message (resume path)
      5. Repeat until no PauseForApproval is raised

    Returns
    ───────
    NextActionDecision — the agent's final structured output.
    """
    from agents import Runner
    from app.tools import workflow_store, PauseForApproval

    current_input = message
    iteration = 0
    max_iterations = 10

    while iteration < max_iterations:
        iteration += 1
        console.print(f"\n[dim]🔄 Agent run iteration {iteration}...[/dim]")

        try:
            trace_id = collector.trace_id if collector else "no-trace"
            with sdk_trace("InvoiceWorkflow", trace_id):
                result = await Runner.run(agent, current_input)

        except PauseForApproval as exc:
            # ── HITL pause — human approval needed ────────────────────────
            console.print("[yellow]⏸  Agent paused — human approval required.[/yellow]")

            if collector:
                collector.record(
                    tool_name="request_approval",
                    input_data={"invoice_id": exc.invoice_id, "amount": exc.amount},
                    output_data={"approval_status": "pending"},
                    duration_ms=0,
                    success=True,
                )

            decision = handle_interruptions(exc)
            color = "green" if decision == "approved" else "red"
            icon  = "✅" if decision == "approved" else "❌"
            console.print(f"\n{icon} [bold {color}]Human decision: {decision.upper()}[/bold {color}]")

            # Write decision into workflow_store (resume path)
            workflow_store.setdefault(exc.invoice_id, {})["approval_status"] = decision

            if decision == "approved":
                current_input = (
                    current_input
                    + f"\n\nThe human reviewer has APPROVED invoice {exc.invoice_id}."
                    " Proceed to post_to_erp."
                )
            else:
                current_input = (
                    current_input
                    + f"\n\nThe human reviewer has REJECTED invoice {exc.invoice_id}."
                    " Mark the invoice as rejected. Do NOT call post_to_erp."
                )
            continue

        # ── No exception → agent completed ────────────────────────────────
        console.print("[dim]✅ Agent completed.[/dim]")
        return result.final_output

    raise RuntimeError("Agent did not complete after maximum HITL iterations.")


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

    Phase 4 behaviour:
      1. Loads and validates the JSON into an Invoice model
      2. Builds a plain-text message for the agent
      3. Runs run_with_hitl() — handles tool calls AND approval interrupts
      4. Maps the final NextActionDecision to WorkflowState
      5. Saves result to output/<invoice_id>_result.json
      6. Prints a colour-coded summary
    """
    console.rule("[bold blue]Invoice Workflow Agent POC — Phase 5 (Tracing + HITL)[/]")

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

    # ── Step 2: Initialise workflow state + trace collector ───────────────────
    state     = WorkflowState(invoice_id=inv.invoice_id, status=WorkflowStatus.IN_PROGRESS)
    scenario  = _infer_scenario(inv.invoice_amount)
    collector = TraceCollector(invoice_id=inv.invoice_id, scenario=scenario)
    console.print(f"[dim]📊 Tracing enabled — scenario: {scenario} | trace_id: {collector.trace_id}[/dim]\n")

    # ── Step 3: Run the agent (with HITL + tracing) ───────────────────────────
    console.print("\n[cyan]🤖 Running Invoice Agent (Phase 5 — Tracing + HITL) ...[/]\n")
    message = build_invoice_message(inv)

    try:
        from app.agent import invoice_agent
        decision: NextActionDecision = asyncio.run(
            run_with_hitl(invoice_agent, message, collector=collector)
        )

    except Exception as exc:
        console.print(f"[red]Agent error:[/] {exc}")
        state.escalate(reason=f"Agent error: {exc}")
        save_result(state)
        trace_path = collector.save()
        console.print(f"[dim]Trace saved to:[/] {trace_path}\n")
        raise typer.Exit(code=1)

    # ── Step 4: Apply decision to state ───────────────────────────────────────
    apply_decision_to_state(state, decision, inv)

    # ── Step 5: Save result + trace ───────────────────────────────────────────
    out_path   = save_result(state)
    trace_path = collector.save()
    print_workflow_result(state, decision)

    # ── Step 6: Print trace summary ───────────────────────────────────────────
    trace_data = collector.to_dict()
    summary_text = generate_trace_summary(trace_data)
    console.print(Panel(
        summary_text,
        title="📊 Execution Trace Summary",
        border_style="dim cyan",
    ))
    console.print(f"\n[dim]Result saved to:[/] {out_path}")
    console.print(f"[dim]Trace  saved to:[/] {trace_path}\n")


if __name__ == "__main__":
    app()


# ─────────────────────────────────────────────────────────────────────────────
# Private Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _infer_scenario(amount: float) -> str:
    """Map invoice amount to a human-readable scenario tag for the trace."""
    import app.config as _cfg
    if amount >= _cfg.APPROVAL_THRESHOLD:
        return "approval_path"
    return "happy_path"
