"""
app/config.py — Configuration and constants for the Invoice Workflow Agent POC.

Loads environment variables from .env and exposes typed configuration values
and mock data used by the invoice processing tools.
"""

import os
from dotenv import load_dotenv

# Load .env file at import time
load_dotenv()

# ── OpenAI Settings ──────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

# ── Workflow Thresholds ──────────────────────────────────────────────────────
# Invoices exceeding this amount (INR) must go through human approval
APPROVAL_THRESHOLD: float = float(os.getenv("APPROVAL_THRESHOLD", "100000"))

# ── Tracing & Output ─────────────────────────────────────────────────────────
ENABLE_TRACING: bool = os.getenv("ENABLE_TRACING", "true").lower() == "true"
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "output")

# ── Mock Master Data ─────────────────────────────────────────────────────────
# Registered vendors: vendor_name → vendor_gstin
VALID_VENDORS: dict[str, str] = {
    "Tech Supplies Ltd": "29ABCDE1234F1Z5",
    "Office Mart India": "27ZYXWV9876G2A3",
    "Cloud Services Inc": "07LMNOP5678H3B4",
    # Added for Sliced Invoices demo testing
    "Demo - Sliced Invoices": "00DEMO0000D1Z1A",
}

# Valid POs: po_number → approved_amount (INR)
VALID_POS: dict[str, float] = {
    "PO-45678": 150_000.0,
    "PO-12345": 50_000.0,
    "PO-99999": 200_000.0,
    # Order Number 12345 from the Sliced Invoices demo invoice (USD 93.50 ≈ INR 7,800)
    "PO-SI-12345": 10_000.0,
}

# ── In-memory duplicate detection store ─────────────────────────────────────
# Populated at runtime as invoices are processed.
# In production this would be a database table.
PROCESSED_INVOICES: set[str] = set()
