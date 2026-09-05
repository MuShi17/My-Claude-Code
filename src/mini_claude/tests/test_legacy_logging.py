"""Regression tests for the legacy logger/tracer compatibility sink."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import mini_claude.logger as logger_module
from mini_claude.logger import AgentLogger, format_utc_milliseconds
from mini_claude.tracer import SessionTracer


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_child_logger_writes_to_parent_ask_with_child_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(logger_module, "SESSION_DIR", tmp_path / "sessions")
    parent = AgentLogger("session-001", agent_id="main")
    child = AgentLogger(
        "child-session-isolation-id",
        agent_id="main.explore_1",
        parent_logger=parent,
    )
    parent.new_ask(1)
    child.new_ask()
    child.log_api_request("req-child", "fixture-model")
    child.log_error("req-child", "tool_failed", "fixture failure")
    parent.close()

    events = _read_jsonl(tmp_path / "sessions" / "session-001" / "logs" / "001.jsonl")
    assert [event["type"] for event in events] == ["api_request", "error"]
    assert all(event["agent_id"] == "main.explore_1" for event in events)
    assert all(event["parent_agent_id"] == "main" for event in events)
    assert all(event["ask_index"] == 1 for event in events)


def test_logger_preserves_injected_utc_milliseconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(logger_module, "SESSION_DIR", tmp_path / "sessions")
    timestamps = iter(
        [
            datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=timezone.utc),
            datetime(2026, 1, 2, 3, 4, 5, 987000, tzinfo=timezone.utc),
        ]
    )
    logger = AgentLogger("session-002", clock=lambda: next(timestamps))
    logger.new_ask(1)
    logger.log_api_request("req-1", "model")
    logger.log_error("req-1", "fixture", "message")
    logger.close()

    events = _read_jsonl(tmp_path / "sessions" / "session-002" / "logs" / "001.jsonl")
    assert events[0]["timestamp"] == "2026-01-02T03:04:05.123Z"
    assert events[1]["timestamp"] == "2026-01-02T03:04:05.987Z"
    assert format_utc_milliseconds(
        datetime(2026, 1, 2, 3, 4, 5, 1_000, tzinfo=timezone.utc)
    ) == "2026-01-02T03:04:05.001Z"


def test_llm_reference_is_written_only_after_capture_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(logger_module, "SESSION_DIR", tmp_path / "sessions")
    logger = AgentLogger("session-003")
    logger.new_ask(1)
    ref = logger.save_llm_content(
        "req-ok", "fixture-model", [{"role": "user", "content": "hi"}],
        {"content": "ok"}, {"input_tokens": 1, "output_tokens": 1},
    )
    logger.log_api_response("req-ok", 4, 1, 1, llm_ref=ref, llm_capture_status="saved")
    logger.close()

    session_dir = tmp_path / "sessions" / "session-003"
    llm = _read_jsonl(session_dir / "llm" / "session-003.jsonl")
    logs = _read_jsonl(session_dir / "logs" / "001.jsonl")
    assert llm[0]["llm_ref"] == "req-ok"
    assert logs[0]["llm_ref"] == "req-ok"

    failing = AgentLogger("session-004")
    failing.new_ask(1)
    original_ensure_llm_file = failing._ensure_llm_file
    monkeypatch.setattr(failing, "_ensure_llm_file", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        failing.save_llm_content("req-fail", "model", [], {}, {})
    # The caller can still record a response, but without a false reference.
    monkeypatch.setattr(failing, "_ensure_llm_file", original_ensure_llm_file)
    failing.log_api_response(
        "req-fail", 2, 0, 0, llm_ref=None, llm_capture_status="failed"
    )
    failing.close()
    failed = _read_jsonl(tmp_path / "sessions" / "session-004" / "logs" / "001.jsonl")
    assert "llm_ref" not in failed[0]
    assert failed[0]["llm_capture_status"] == "failed"


def test_tracer_rewrites_turn_after_tool_details_and_summary_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(logger_module, "SESSION_DIR", tmp_path / "sessions")
    logger = AgentLogger("session-005")
    logger.new_ask(1)
    tracer = SessionTracer(1, "read file", logger)
    tracer.on_turn_start({"turn_index": 1})
    tracer.on_turn_end(
        {"turn_index": 1, "input_tokens": 10, "output_tokens": 5, "finish_reason": "tool_use"}
    )
    tracer.on_tool_end(
        {
            "tool_name": "read_file",
            "tool_input": {"file_path": "sample.txt"},
            "duration_ms": 7,
            "result_length": 12,
            "success": True,
            "tool_call_id": "call-1",
        }
    )
    tracer.on_tool_end(
        {
            "tool_name": "grep_search",
            "tool_input": {"pattern": "TODO"},
            "duration_ms": 3,
            "result_length": 20,
            "success": False,
            "error_type": "tool_failed",
        }
    )
    tracer.write_ask_summary()
    tracer.write_ask_summary()
    logger.close()

    lines = _read_jsonl(tmp_path / "sessions" / "session-005" / "traces" / "001.jsonl")
    assert [line["type"] for line in lines] == ["turn", "ask"]
    turn = lines[0]
    assert len(turn["tool_calls"]) == 2
    assert turn["tool_calls"][0]["tool_call_id"] == "call-1"
    assert turn["tool_calls"][1]["error_type"] == "tool_failed"
    assert lines[1]["total_tool_calls"] == 2
    assert lines[1]["successful_tools"] == 1
