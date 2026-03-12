# Invoice Workflow Agent POC

A proof-of-technology demonstrating how the **OpenAI Agents SDK** orchestrates
a multi-step invoice processing workflow with tool calling, structured outputs,
human-in-the-loop approval, guardrails, and execution tracing.

---

## Quick Start

### 1 — Clone & Environment Setup

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Install all dependencies
pip install -r requirements.txt
```

### 2 — Configure API Key

```bash
# Copy the template
copy .env.example .env

# Open .env and set your key:
# OPENAI_API_KEY=sk-...your-key...
```

### 3 — Verify SDK Installation

```bash
python scripts/hello_world_agent.py
```

Expected output:
```
✅ OPENAI_API_KEY found
✅ SDK import successful
✅ Agent created successfully
✅ Agent responded: Hello! I'm ready to process invoices.
🎉 Day 1 verification PASSED
```

### 4 — Run the Three Test Scenarios

```bash
# Scenario 1 — Happy Path (₹45,000, no approval needed)
python -m app.main run --invoice sample_data/invoice_happy.json

# Scenario 2 — Approval Path (₹1,25,000, HITL approval required)
python -m app.main run --invoice sample_data/invoice_approval.json

# Scenario 3 — Failure Path (invalid vendor → rejected)
python -m app.main run --invoice sample_data/invoice_failure.json
```

Each run saves two output files:
```
output/<invoice_id>_result.json   ← final workflow state
output/<invoice_id>_trace.json    ← full tool-call trace with timings
```

### 5 — Run Tests

```bash
# All phases
python -m pytest tests/ -v

# Individual phases
python -m pytest tests/test_phase1.py -v   # Day 1 setup tests
python -m pytest tests/test_tools.py -v   # Day 2 tool unit tests
python -m pytest tests/test_phase3.py -v  # Day 3 agent tests
python -m pytest tests/test_phase4.py -v  # Day 4 HITL + guardrail tests
python -m pytest tests/test_phase5.py -v  # Day 5 tracing + API tests
```

### 6 — Start the FastAPI Server (Optional Bonus)

```bash
uvicorn app.api:api --reload --port 8000
```

Test it:
```bash
# Health check
curl http://localhost:8000/health

# Submit an invoice
curl -X POST http://localhost:8000/process-invoice \
     -H "Content-Type: application/json" \
     -d @sample_data/invoice_happy.json

# Get the trace
curl http://localhost:8000/trace/INV-2024-HAPPY-001

# Interactive docs
open http://localhost:8000/docs
```

---

## Project Structure

```
Invoice-Agent-POC/
├── README.md
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py        # CLI entry point (HITL + tracing)
│   ├── agent.py       # Agent config, tools, instructions
│   ├── tools.py       # Mock tool implementations + PauseForApproval
│   ├── guardrails.py  # ERPPostGuardrail (Phase 4)
│   ├── tracing.py     # TraceCollector, span(), sdk_trace() (Phase 5)
│   ├── api.py         # FastAPI REST endpoints (Phase 5 bonus)
│   ├── state.py       # Pydantic schemas (Invoice, WorkflowState, etc.)
│   └── config.py      # Constants, mock data, env loading
├── sample_data/
│   ├── invoice_happy.json      # ₹45,000 — no approval needed
│   ├── invoice_approval.json   # ₹1,25,000 — HITL approval required
│   └── invoice_failure.json    # Invalid vendor — rejected immediately
├── output/
│   └── (generated at runtime — result + trace JSON per invoice)
├── scripts/
│   └── hello_world_agent.py   # SDK smoke test
└── tests/
    ├── test_phase1.py   # Day 1 setup + schema tests
    ├── test_tools.py    # Day 2 tool unit tests
    ├── test_phase3.py   # Day 3 agent + structured output tests
    ├── test_phase4.py   # Day 4 HITL + guardrail tests (20 tests)
    └── test_phase5.py   # Day 5 tracing + FastAPI tests (22 tests)
```

---

## Test Scenarios

| Scenario | File | Invoice Amount | Expected Status |
|---|---|---|---|
| Happy Path | `invoice_happy.json` | ₹45,000 | `completed` (no approval needed) |
| Approval Path | `invoice_approval.json` | ₹1,25,000 | `completed` (after human approval) |
| Failure Path | `invoice_failure.json` | ₹80,000 | `rejected` (invalid vendor) |

---

## SDK Features Demonstrated

| Feature | Day | File | Status |
|---|---|---|---|
| Agent + Tool calling | Day 2-3 | `app/agent.py`, `app/tools.py` | ✅ |
| Structured Outputs (`NextActionDecision`) | Day 3 | `app/state.py` | ✅ |
| Human-in-the-Loop (custom `PauseForApproval`) | Day 4 | `app/tools.py`, `app/main.py` | ✅ |
| Input Guardrails (`ERPPostGuardrail`) | Day 4 | `app/guardrails.py` | ✅ |
| Execution Tracing (`TraceCollector` + `sdk_trace`) | Day 5 | `app/tracing.py` | ✅ |
| REST API (`FastAPI`) | Day 5 | `app/api.py` | ✅ |

---

## How Tracing Works

Every CLI run automatically produces a trace JSON in `output/`. Example:

```json
{
  "trace_id": "a1b2c3d4-...",
  "invoice_id": "INV-2024-HAPPY-001",
  "scenario": "happy_path",
  "started_at": "2026-03-12T14:30:00Z",
  "total_duration_ms": 4521.3,
  "tool_calls_count": 4,
  "events": [
    {"seq": 1, "tool_name": "validate_vendor", "duration_ms": 0.8, "success": true},
    {"seq": 2, "tool_name": "validate_po",     "duration_ms": 0.6, "success": true},
    {"seq": 3, "tool_name": "check_duplicate", "duration_ms": 0.4, "success": true},
    {"seq": 4, "tool_name": "post_to_erp",     "duration_ms": 1.2, "success": true}
  ]
}
```

With `ENABLE_TRACING=true` in `.env`, agent spans are also sent to the
[OpenAI Platform dashboard](https://platform.openai.com/traces) so you can
inspect LLM calls, token usage, and latency online.

---

## Human-in-the-Loop (HITL) Design

Invoices exceeding ₹1,00,000 require human approval. The flow:

```
Agent calls request_approval()
    ↓ raises PauseForApproval
main.py catches it → shows approval panel
    ↓ human types "approve" or "reject"
Decision written to workflow_store
    ↓ Agent re-run with decision in message
Resume path: request_approval() reads store → returns ApprovalOutput
    ↓ Agent proceeds to post_to_erp (if approved)
```

---

## Day-by-Day Progress

| Day | What was built | Status |
|---|---|---|
| Day 1 | Environment, schema design, sample data, project structure | ✅ |
| Day 2 | Mock tool implementations + unit tests | ✅ |
| Day 3 | Agent core, structured outputs, happy path E2E | ✅ |
| Day 4 | HITL approval (PauseForApproval), ERPPostGuardrail | ✅ |
| Day 5 | Tracing, FastAPI, full scenario tests, documentation | ✅ |

---

## Resources

- [OpenAI Agents SDK Docs](https://openai.github.io/openai-agents-python/)
- [Tracing Guide](https://openai.github.io/openai-agents-python/tracing/)
- [Structured Outputs Guide](https://platform.openai.com/docs/guides/structured-outputs)
- [Pydantic Docs](https://docs.pydantic.dev/)
- [FastAPI Docs](https://fastapi.tiangolo.com/)