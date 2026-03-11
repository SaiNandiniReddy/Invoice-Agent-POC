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

### 4 — Run the CLI (Day 1 — schema validation only)

```bash
python -m app.main --invoice sample_data/invoice_happy.json
python -m app.main --invoice sample_data/invoice_approval.json
python -m app.main --invoice sample_data/invoice_failure.json
```

### 5 — Run Tests

```bash
pytest tests/test_phase1.py -v
```

---

## Project Structure

```
invoice-agent-poc/
├── README.md
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py         # CLI entry point
│   ├── agent.py        # Agent config and instructions
│   ├── tools.py        # Mock tool implementations
│   ├── state.py        # Pydantic schemas (Invoice, WorkflowState, NextActionDecision)
│   ├── guardrails.py   # ERP post guardrail
│   └── config.py       # Constants, mock master data, env loading
├── sample_data/
│   ├── invoice_happy.json       # Happy path (below threshold, valid vendor)
│   ├── invoice_approval.json    # Approval path (above threshold)
│   └── invoice_failure.json     # Failure path (invalid vendor)
├── scripts/
│   └── hello_world_agent.py    # SDK installation smoke test
├── output/
│   └── (generated at runtime)
└── tests/
    ├── __init__.py
    └── test_phase1.py           # Day 1 automated tests (offline, no API calls)
```

---

## Test Scenarios

| Scenario | File | Invoice Amount | Expected Status |
|----------|------|---------------|-----------------|
| Happy Path | `invoice_happy.json` | ₹45,000 | `completed` |
| Approval Path | `invoice_approval.json` | ₹1,25,000 | `completed` (after approval) |
| Failure Path | `invoice_failure.json` | ₹80,000 | `rejected` |

---

## SDK Features Demonstrated

| Feature | Status | File |
|---------|--------|------|
| Function Tools (5–6 with typed I/O) | Day 2 | `app/tools.py` |
| Agent Loop (multi-step tool calls) | Day 3 | `app/agent.py` |
| Structured Outputs (NextActionDecision) | Day 3 | `app/state.py` |
| Human-in-the-Loop (interrupt/resume) | Day 4 | `app/tools.py` |
| Guardrails (ERP post validation) | Day 4 | `app/guardrails.py` |
| Execution Tracing (JSON export) | Day 5 | `app/main.py` |

---

## Day-by-Day Progress

- **Day 1** ✅ — Environment, schema design, sample data, project structure
- **Day 2** ⬜ — Mock tool implementations + unit tests
- **Day 3** ⬜ — Agent core, structured outputs, happy path E2E
- **Day 4** ⬜ — Human-in-the-loop approval, guardrails
- **Day 5** ⬜ — Tracing, full scenario testing, documentation

---

## Resources

- [OpenAI Agents SDK Docs](https://openai.github.io/openai-agents-python/)
- [Human-in-the-Loop Guide](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- [Structured Outputs Guide](https://platform.openai.com/docs/guides/structured-outputs)
- [Pydantic Docs](https://docs.pydantic.dev/)