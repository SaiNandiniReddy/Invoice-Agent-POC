"""
app/main.py — CLI Entry Point for the Invoice Workflow Agent POC.

Day 1 Status: SKELETON — loads and validates invoice JSON, prints structured
              data to confirm schemas work. Full agent orchestration wiring
              happens on Day 3.

Usage:
    python -m app.main --invoice sample_data/invoice_happy.json
    python -m app.main --invoice sample_data/invoice_approval.json
    python -m app.main --invoice sample_data/invoice_failure.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.config import OUTPUT_DIR
from app.state import Invoice, WorkflowState, WorkflowStatus

app    = typer.Typer(help="Invoice Workflow Agent POC — CLI")
console = Console()


def load_invoice(path: Path) -> Invoice:
    """Load and validate an invoice JSON file into an Invoice Pydantic model."""
    if not path.exists():
        console.print(f"[red]ERROR:[/] Invoice file not found: {path}")
        raise typer.Exit(code=1)

    raw = json.loads(path.read_text(encoding="utf-8"))
    return Invoice(**raw)


def print_invoice_summary(invoice: Invoice) -> None:
    """Pretty-print an invoice using a Rich table."""
    table = Table(title=f"📄 Invoice: {invoice.invoice_id}", show_header=True, header_style="bold cyan")
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


def save_result(state: WorkflowState) -> Path:
    """Save the final workflow state JSON to the output directory."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = Path(OUTPUT_DIR) / f"{state.invoice_id}_result.json"
    out_path.write_text(
        state.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return out_path


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

    Day 1 behaviour: Loads, validates, and displays the invoice.
    Full agent orchestration is wired in Day 3.
    """
    console.rule("[bold blue]Invoice Workflow Agent POC[/]")

    # Step 1: Load & Validate Invoice
    console.print(f"\n[yellow]Loading invoice:[/] {invoice}\n")
    inv = load_invoice(invoice)
    print_invoice_summary(inv)

    # Step 2: Initialize Workflow State
    state = WorkflowState(invoice_id=inv.invoice_id, status=WorkflowStatus.PENDING)
    console.print(
        Panel(
            f"[green]Invoice loaded successfully.[/]\n"
            f"Status: [bold]{state.status.value}[/]\n"
            f"[dim](Agent orchestration will be wired in Day 3)[/]",
            title="Workflow State",
            border_style="green",
        )
    )

    # Step 3: Save initial state
    out_path = save_result(state)
    console.print(f"\n[dim]Result saved to:[/] {out_path}\n")


if __name__ == "__main__":
    app()
