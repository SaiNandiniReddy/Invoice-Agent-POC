"""
tests/test_phase5.py — Phase 5 Unit Tests: Tracing, Output & FastAPI

╔══════════════════════════════════════════════════════════════════════════════╗
║  JUNIOR DEVELOPER GUIDE                                                    ║
║                                                                            ║
║  This file tests FOUR things introduced in Phase 5:                       ║
║                                                                            ║
║  1. TraceCollector                                                         ║
║     - Does it record tool events with the right fields?                   ║
║     - Does it compute durations correctly?                                 ║
║     - Does it save a valid JSON file?                                      ║
║                                                                            ║
║  2. span() context manager                                                 ║
║     - Does it capture success results?                                     ║
║     - Does it record errors without swallowing exceptions?                 ║
║                                                                            ║
║  3. generate_trace_summary()                                               ║
║     - Does it produce a readable summary string?                           ║
║                                                                            ║
║  4. FastAPI endpoints                                                      ║
║     - /health — does it return 200 OK?                                    ║
║     - /trace/{invoice_id} — 404 when no trace exists?                    ║
║                                                                            ║
║  HOW TO RUN:                                                               ║
║      cd Invoice-Agent-POC                                                  ║
║      python -m pytest tests/test_phase5.py -v                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

Key concepts used in these tests
──────────────────────────────────
tmp_path  : A pytest fixture that gives you a fresh temporary directory
            for each test. Files written there are cleaned up automatically.

TestClient: A FastAPI test helper that simulates HTTP requests without
            starting a real server. Import it from fastapi.testclient.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.tracing import (
    TraceCollector,
    generate_trace_summary,
    span,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def collector(tmp_path) -> TraceCollector:
    """
    Create a fresh TraceCollector for each test.
    Uses tmp_path so trace JSON files go to a temp dir, not output/.
    """
    c = TraceCollector(invoice_id="INV-TEST-001", scenario="happy_path")
    c._output_dir = str(tmp_path)  # redirect output to temp folder
    return c


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — TraceCollector
# ═════════════════════════════════════════════════════════════════════════════

class TestTraceCollector:
    """
    Tests for the TraceCollector class.

    TraceCollector is the "black box recorder" that watches what the agent
    does during a run and stores everything in a structured dict.
    """

    INVOICE_ID = "INV-TRACE-001"
    SCENARIO   = "happy_path"

    def _make(self) -> TraceCollector:
        return TraceCollector(invoice_id=self.INVOICE_ID, scenario=self.SCENARIO)

    # ── Test 1A: Basic fields are set on creation ────────────────────────────
    def test_initial_fields(self):
        """
        GIVEN: A new TraceCollector is created
        THEN:  invoice_id, scenario, trace_id, and started_at are set correctly
        """
        c = self._make()
        assert c.invoice_id == self.INVOICE_ID
        assert c.scenario == self.SCENARIO
        assert len(c.trace_id) > 0, "trace_id should be a non-empty UUID string"
        assert c.started_at is not None
        assert c.events == []

    # ── Test 1B: record() appends a correctly shaped event ───────────────────
    def test_record_appends_event(self):
        """
        GIVEN: A TraceCollector with no events
        WHEN:  record() is called once
        THEN:  events list has one entry with all required keys
        """
        c = self._make()
        c.record(
            tool_name="validate_vendor",
            input_data={"vendor_name": "Tech Supplies"},
            output_data={"is_valid": True},
            duration_ms=1.23,
            success=True,
        )
        assert len(c.events) == 1
        ev = c.events[0]
        assert ev["tool_name"] == "validate_vendor"
        assert ev["seq"] == 1
        assert ev["success"] is True
        assert ev["duration_ms"] == 1.23
        assert "timestamp" in ev
        assert "input" in ev
        assert "output" in ev

    # ── Test 1C: Multiple records get sequential seq numbers ─────────────────
    def test_record_sequence_numbers(self):
        """
        GIVEN: Three tool calls recorded
        THEN:  seq numbers are 1, 2, 3 in order
        """
        c = self._make()
        for i, name in enumerate(["validate_vendor", "validate_po", "check_duplicate"], 1):
            c.record(name, {}, {}, 0.5, True)
            assert c.events[-1]["seq"] == i

    # ── Test 1D: to_dict() returns correct structure ─────────────────────────
    def test_to_dict_structure(self):
        """
        GIVEN: A collector with 2 recorded events
        WHEN:  to_dict() is called
        THEN:  All top-level keys are present and tool_calls_count is correct
        """
        c = self._make()
        c.record("tool_a", {}, {}, 1.0, True)
        c.record("tool_b", {}, {}, 2.0, True)
        c.finish()

        d = c.to_dict()
        assert d["invoice_id"] == self.INVOICE_ID
        assert d["scenario"]   == self.SCENARIO
        assert d["tool_calls_count"] == 2
        assert len(d["events"]) == 2
        assert "trace_id" in d
        assert "started_at" in d
        assert "total_duration_ms" in d

    # ── Test 1E: save() writes a valid JSON file ─────────────────────────────
    def test_save_writes_json(self, tmp_path):
        """
        GIVEN: A collector with one event
        WHEN:  save(output_dir=tmp_path) is called
        THEN:  A JSON file is created at tmp_path/INV-TRACE-001_trace.json
               The file is valid JSON with the correct invoice_id
        """
        c = self._make()
        c.record("validate_vendor", {"vendor": "X"}, {"is_valid": True}, 0.5, True)

        saved_path = c.save(output_dir=str(tmp_path))

        assert saved_path.exists(), "save() should create the trace file"
        data = json.loads(saved_path.read_text(encoding="utf-8"))
        assert data["invoice_id"] == self.INVOICE_ID
        assert data["tool_calls_count"] == 1
        assert len(data["events"]) == 1

    # ── Test 1F: Error events are recorded correctly ─────────────────────────
    def test_record_error_event(self):
        """
        GIVEN: A tool call that raised an exception
        WHEN:  record() is called with success=False and an error message
        THEN:  The event captures success=False and the error text
        """
        c = self._make()
        c.record(
            tool_name="post_to_erp",
            input_data={},
            output_data=None,
            duration_ms=0.1,
            success=False,
            error="Guardrail blocked the call",
        )
        ev = c.events[0]
        assert ev["success"] is False
        assert "Guardrail blocked" in ev["error"]

    # ── Test 1G: total_duration_ms is positive ───────────────────────────────
    def test_total_duration_increases_over_time(self):
        """
        GIVEN: A TraceCollector just created
        WHEN:  Some time passes
        THEN:  total_duration_ms() returns a positive number
        """
        c = self._make()
        time.sleep(0.01)  # 10ms sleep
        duration = c.total_duration_ms()
        assert duration > 0, "Duration must be positive after any time has passed"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — span() context manager
# ═════════════════════════════════════════════════════════════════════════════

class TestSpanContextManager:
    """
    Tests for the span() context manager.

    span() is a helper that times a tool call and writes the result to the
    TraceCollector automatically. It handles both success and failure cases.
    """

    INVOICE_ID = "INV-SPAN-001"

    def _make(self) -> TraceCollector:
        return TraceCollector(invoice_id=self.INVOICE_ID, scenario="test")

    # ── Test 2A: Success — result is recorded ────────────────────────────────
    def test_span_records_success(self):
        """
        GIVEN: A tool call that succeeds
        WHEN:  span() context manager wraps it
        THEN:  One event is recorded with success=True and the correct output
        """
        c = self._make()
        with span(c, "validate_vendor", {"vendor": "Tech"}) as result:
            result.append({"is_valid": True, "reason": "OK"})

        assert len(c.events) == 1
        ev = c.events[0]
        assert ev["tool_name"] == "validate_vendor"
        assert ev["success"] is True
        assert ev["output"]["is_valid"] is True
        assert ev["duration_ms"] >= 0

    # ── Test 2B: Error — exception is recorded AND re-raised ─────────────────
    def test_span_records_and_reraises_exception(self):
        """
        GIVEN: A tool call that raises an exception
        WHEN:  span() wraps it
        THEN:  The exception is re-raised (so calling code can handle it)
               AND an event is recorded with success=False
        """
        c = self._make()
        with pytest.raises(ValueError, match="something broke"):
            with span(c, "post_to_erp", {}):
                raise ValueError("something broke")

        assert len(c.events) == 1
        ev = c.events[0]
        assert ev["success"] is False
        assert "something broke" in ev["error"]

    # ── Test 2C: Multiple spans accumulate in order ───────────────────────────
    def test_multiple_spans_accumulate(self):
        """
        GIVEN: Three successive span() calls
        THEN:  Three events are recorded in sequence
        """
        c = self._make()
        for tool in ["validate_vendor", "validate_po", "post_to_erp"]:
            with span(c, tool, {}) as r:
                r.append("ok")

        assert len(c.events) == 3
        names = [ev["tool_name"] for ev in c.events]
        assert names == ["validate_vendor", "validate_po", "post_to_erp"]


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — generate_trace_summary()
# ═════════════════════════════════════════════════════════════════════════════

class TestGenerateTraceSummary:
    """
    Tests for the generate_trace_summary() helper.

    This function takes a trace dict and produces a human-readable
    multi-line string that gets printed at the end of each CLI run.
    """

    def _make_trace(self, events=None) -> dict:
        """Build a minimal trace dict for testing."""
        return {
            "trace_id":         "test-trace-id",
            "invoice_id":       "INV-SUM-001",
            "scenario":         "happy_path",
            "started_at":       "2026-03-12T00:00:00Z",
            "total_duration_ms": 123.45,
            "tool_calls_count": len(events or []),
            "events": events or [],
        }

    # ── Test 3A: Summary contains key fields ─────────────────────────────────
    def test_summary_contains_key_fields(self):
        """
        GIVEN: A trace dict with invoice_id and scenario
        WHEN:  generate_trace_summary() is called
        THEN:  The output string contains the invoice_id, scenario, and duration
        """
        trace = self._make_trace()
        summary = generate_trace_summary(trace)
        assert "INV-SUM-001" in summary
        assert "happy_path" in summary
        assert "123.45" in summary

    # ── Test 3B: Tool call sequence is listed ────────────────────────────────
    def test_summary_lists_tool_calls(self):
        """
        GIVEN: A trace with two tool call events
        WHEN:  generate_trace_summary() is called
        THEN:  Both tool names appear in the summary output
        """
        events = [
            {"seq": 1, "tool_name": "validate_vendor", "success": True,
             "duration_ms": 0.5, "error": None},
            {"seq": 2, "tool_name": "post_to_erp", "success": True,
             "duration_ms": 1.2, "error": None},
        ]
        trace = self._make_trace(events=events)
        summary = generate_trace_summary(trace)
        assert "validate_vendor" in summary
        assert "post_to_erp" in summary

    # ── Test 3C: Failed steps are marked visibly ─────────────────────────────
    def test_summary_marks_failures(self):
        """
        GIVEN: A trace with one failed tool call
        WHEN:  generate_trace_summary() is called
        THEN:  The failure is marked with ❌ and the error message appears
        """
        events = [
            {"seq": 1, "tool_name": "post_to_erp", "success": False,
             "duration_ms": 0.1, "error": "Guardrail blocked this call"},
        ]
        trace = self._make_trace(events=events)
        summary = generate_trace_summary(trace)
        assert "❌" in summary
        assert "Guardrail blocked" in summary


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — FastAPI Endpoints
# ═════════════════════════════════════════════════════════════════════════════

class TestFastAPIEndpoints:
    """
    Tests for the FastAPI REST API (app/api.py).

    We use FastAPI's TestClient to make HTTP requests without starting a
    real server — it's all in-memory and very fast.
    """

    @pytest.fixture(autouse=True)
    def client(self):
        """Create a TestClient for each test."""
        from fastapi.testclient import TestClient
        from app.api import api
        self._client = TestClient(api)

    # ── Test 4A: /health returns 200 OK ──────────────────────────────────────
    def test_health_returns_200(self):
        """
        GIVEN: The API server is running
        WHEN:  GET /health is called
        THEN:  Response is 200 OK with status="ok"
        """
        response = self._client.get("/health")
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}"
        )
        data = response.json()
        assert data["status"] == "ok"
        assert "service" in data

    # ── Test 4B: /trace/{id} returns 404 when no trace exists ────────────────
    def test_trace_not_found_returns_404(self):
        """
        GIVEN: No invoice has been processed yet
        WHEN:  GET /trace/INV-NOPE is called
        THEN:  Response is 404 Not Found
        """
        response = self._client.get("/trace/INV-NOPE-12345")
        assert response.status_code == 404, (
            "Should return 404 when trace file doesn't exist"
        )
        data = response.json()
        assert "detail" in data

    # ── Test 4C: /result/{id} returns 404 when no result exists ─────────────
    def test_result_not_found_returns_404(self):
        """
        GIVEN: No invoice has been processed yet
        WHEN:  GET /result/INV-NOPE is called
        THEN:  Response is 404 Not Found
        """
        response = self._client.get("/result/INV-NOPE-12345")
        assert response.status_code == 404

    # ── Test 4D: /trace/{id} returns 200 when trace file exists ─────────────
    def test_trace_found_returns_200(self, tmp_path, monkeypatch):
        """
        GIVEN: A trace JSON file exists for INV-DEMO-001
        WHEN:  GET /trace/INV-DEMO-001 is called
        THEN:  Response is 200 with the trace data
        """
        import app.config as cfg
        # Write a fake trace file to the output dir
        monkeypatch.setattr(cfg, "OUTPUT_DIR", str(tmp_path))
        trace_data = {
            "trace_id": "abc-123",
            "invoice_id": "INV-DEMO-001",
            "scenario": "happy_path",
            "started_at": "2026-03-12T00:00:00Z",
            "finished_at": "2026-03-12T00:00:01Z",
            "total_duration_ms": 500.0,
            "tool_calls_count": 2,
            "events": [],
        }
        trace_file = tmp_path / "INV-DEMO-001_trace.json"
        trace_file.write_text(json.dumps(trace_data), encoding="utf-8")

        response = self._client.get("/trace/INV-DEMO-001")
        assert response.status_code == 200
        data = response.json()
        assert data["invoice_id"] == "INV-DEMO-001"
        assert data["trace_id"]   == "abc-123"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Output File Structure
# ═════════════════════════════════════════════════════════════════════════════

class TestOutputFileStructure:
    """
    Tests that verify the output files (result JSON and trace JSON) have
    the correct structure.

    These are important because the FastAPI /result and /trace endpoints
    serve these files directly — corrupted files would cause API errors.
    """

    INVOICE_ID = "INV-OUTPUT-001"

    def test_trace_json_has_required_keys(self, tmp_path):
        """
        GIVEN: A TraceCollector that has run one tool
        WHEN:  save() is called
        THEN:  The saved JSON has all required top-level keys
        """
        c = TraceCollector(invoice_id=self.INVOICE_ID, scenario="happy_path")
        c.record("validate_vendor", {"v": "Tech"}, {"is_valid": True}, 1.0, True)
        saved = c.save(output_dir=str(tmp_path))

        data = json.loads(saved.read_text(encoding="utf-8"))
        required_keys = {
            "trace_id", "invoice_id", "scenario",
            "started_at", "finished_at",
            "total_duration_ms", "tool_calls_count", "events",
        }
        for key in required_keys:
            assert key in data, f"Missing required key: '{key}' in trace JSON"

    def test_trace_events_have_required_keys(self, tmp_path):
        """
        GIVEN: A TraceCollector with one event recorded
        WHEN:  save() is called
        THEN:  Each event object has the required fields
        """
        c = TraceCollector(invoice_id=self.INVOICE_ID, scenario="happy_path")
        c.record("validate_vendor", {"v": "Tech"}, {"is_valid": True}, 2.5, True)
        saved = c.save(output_dir=str(tmp_path))

        data = json.loads(saved.read_text(encoding="utf-8"))
        ev = data["events"][0]
        required_keys = {"seq", "tool_name", "timestamp", "duration_ms",
                         "success", "input", "output"}
        for key in required_keys:
            assert key in ev, f"Missing required key: '{key}' in event"
