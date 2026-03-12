"""
app/tracing.py — Execution Tracing for the Invoice Workflow Agent POC.

Phase 5 additions:
  - TraceCollector  : Gathers tool-call events during an agent run.
  - save_trace_json : Writes the collected trace to output/<invoice_id>_trace.json.
  - sdk_trace       : Context manager that wraps Runner.run() in an SDK trace span.

WHY TRACE?
──────────
Traces answer "what did the agent actually do, and how long did each step take?"
Without tracing you only know the final result. With tracing you can:
  - Debug unexpected tool call orders
  - Measure latency per tool
  - Replay a run step-by-step for audit purposes
  - Compare happy / approval / failure scenarios side by side

HOW IT WORKS IN THIS POC
─────────────────────────
The OpenAI Agents SDK provides:
  - agents.trace(workflow_name, trace_id) — context manager that creates a
    top-level "workflow" span visible on the OpenAI dashboard.
  - agents.custom_span(name) — creates child spans for named steps.

We ALSO capture our own JSON trace so the output is self-contained and
does NOT require an OpenAI account to view. This is stored in output/.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import app.config as config


# ─────────────────────────────────────────────────────────────────────────────
# TraceCollector — in-memory event log for one invoice run
# ─────────────────────────────────────────────────────────────────────────────

class TraceCollector:
    """
    Records tool-call events during a single agent run.

    Usage
    ─────
    collector = TraceCollector(invoice_id="INV-001", scenario="happy_path")

    # Record each tool call:
    with collector.span("validate_vendor"):
        result = validate_vendor(input)
        collector.record("validate_vendor", input_data, result)

    # Save at the end:
    collector.save()

    Attributes
    ──────────
    invoice_id : str   — the invoice being processed (used in filename).
    scenario   : str   — e.g. "happy_path", "approval_path", "failure_path".
    events     : list  — list of TraceEvent dicts, one per tool call.
    started_at : str   — ISO-8601 timestamp when the run started.
    """

    def __init__(self, invoice_id: str, scenario: str = "unknown") -> None:
        self.invoice_id  = invoice_id
        self.scenario    = scenario
        self.trace_id    = str(uuid.uuid4())
        self.started_at  = datetime.now(timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.events: list[dict[str, Any]] = []
        self._run_start  = time.perf_counter()

    def record(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        output_data: Any,
        duration_ms: float,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """
        Append a single tool-call event to the trace.

        Parameters
        ──────────
        tool_name   : Name of the tool function called.
        input_data  : Dict of the inputs passed to the tool.
        output_data : The tool's return value (will be JSON-serialised).
        duration_ms : How long the call took in milliseconds.
        success     : True if tool returned normally, False if it raised.
        error       : If success=False, the exception message.
        """
        self.events.append({
            "seq": len(self.events) + 1,         # call order (1-indexed)
            "tool_name": tool_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round(time.perf_counter() - self._run_start, 1) * 1000,
            "duration_ms": round(duration_ms, 2),
            "success": success,
            "error": error,
            "input": input_data,
            "output": _safe_serialise(output_data),
        })

    def finish(self) -> None:
        """Mark the trace as complete (call when the agent run ends)."""
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def total_duration_ms(self) -> float:
        """Total wall-clock time from trace start to finish (ms)."""
        return round((time.perf_counter() - self._run_start) * 1000, 2)

    def to_dict(self) -> dict[str, Any]:
        """Return the full trace as a JSON-serialisable dict."""
        return {
            "trace_id":   self.trace_id,
            "invoice_id": self.invoice_id,
            "scenario":   self.scenario,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms(),
            "tool_calls_count": len(self.events),
            "events": self.events,
        }

    def save(self, output_dir: str | None = None) -> Path:
        """
        Write the trace to output/<invoice_id>_trace.json.

        Returns the path of the saved file.
        """
        self.finish()
        out_dir = Path(output_dir or config.OUTPUT_DIR)
        os.makedirs(out_dir, exist_ok=True)
        out_path = out_dir / f"{self.invoice_id}_trace.json"
        out_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path


# ─────────────────────────────────────────────────────────────────────────────
# span() — context manager for timing an individual tool call
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def span(collector: TraceCollector, tool_name: str, input_data: dict):
    """
    Context manager that times a tool call and records it automatically.

    Usage
    ─────
    with span(collector, "validate_vendor", {"vendor_name": "Tech..."}):
        result = validate_vendor(input)
        # result is automatically captured after the block

    If an exception is raised inside the block, it is recorded as
    success=False and then re-raised so normal error handling continues.

    Example trace event output:
    {
        "seq": 1,
        "tool_name": "validate_vendor",
        "duration_ms": 0.42,
        "success": true,
        "input": {"vendor_name": "Tech Supplies Ltd", ...},
        "output": {"is_valid": true, "reason": "..."}
    }
    """
    t0 = time.perf_counter()
    result_holder: list[Any] = []  # trick: list so inner function can write to it
    error_holder:  list[str] = []

    try:
        yield result_holder  # caller appends result: result_holder.append(val)
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        collector.record(
            tool_name=tool_name,
            input_data=input_data,
            output_data=None,
            duration_ms=duration_ms,
            success=False,
            error=str(exc),
        )
        raise  # re-raise so main.py can handle it
    else:
        duration_ms = (time.perf_counter() - t0) * 1000
        output = result_holder[0] if result_holder else None
        collector.record(
            tool_name=tool_name,
            input_data=input_data,
            output_data=output,
            duration_ms=duration_ms,
            success=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# sdk_trace() — wrap an agent run in an SDK-level trace span
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def sdk_trace(workflow_name: str, trace_id: str):
    """
    Wrap code inside an OpenAI Agents SDK trace span.

    When ENABLE_TRACING=true the SDK sends span data to the OpenAI
    platform dashboard so you can inspect the full agent run online.

    When ENABLE_TRACING=false (or the trace API isn't available) this
    context manager is a no-op — the run still happens, just without
    dashboard tracing.

    Usage
    ─────
    with sdk_trace("InvoiceWorkflow", trace_id=collector.trace_id):
        result = await Runner.run(agent, message)
    """
    if not config.ENABLE_TRACING:
        yield
        return

    try:
        from agents import trace as agents_trace
        with agents_trace(workflow_name, trace_id=trace_id):
            yield
    except Exception:
        # If tracing fails for any reason, don't block the actual run
        yield


# ─────────────────────────────────────────────────────────────────────────────
# generate_trace_summary() — human-readable one-pager
# ─────────────────────────────────────────────────────────────────────────────

def generate_trace_summary(trace: dict) -> str:
    """
    Return a multi-line string summarising a trace dict.

    Used both in tests and in the CLI rich output.
    """
    lines = [
        f"Trace ID   : {trace['trace_id']}",
        f"Invoice    : {trace['invoice_id']}",
        f"Scenario   : {trace['scenario']}",
        f"Started    : {trace['started_at']}",
        f"Duration   : {trace['total_duration_ms']} ms",
        f"Tool calls : {trace['tool_calls_count']}",
        "",
        "Tool call sequence:",
    ]
    for ev in trace.get("events", []):
        status = "✅" if ev["success"] else "❌"
        lines.append(
            f"  {ev['seq']:>2}. {status} {ev['tool_name']:<30} "
            f"({ev['duration_ms']:.2f} ms)"
        )
        if ev.get("error"):
            lines.append(f"       └─ Error: {ev['error']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_serialise(value: Any) -> Any:
    """Convert Pydantic models and other objects to JSON-safe dicts."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):          # Pydantic v2 BaseModel
        return value.model_dump()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_serialise(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe_serialise(v) for k, v in value.items()}
    return str(value)
