"""公共工具结果边界与 read_file 分页契约。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from mini_claude.agent import Agent
from mini_claude.archive_capability import ToolResultArchiveCapability
from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.event_sink import RecordingEventSink, RuntimeEventEmitter
from mini_claude.event_ids import RunContext
from mini_claude.mcp_client import McpConnection
from mini_claude.redaction import RedactionPolicy, redact_payload
from mini_claude.runtime_lifecycle import DurableToolBoundary
from mini_claude.runtime_event import thaw
from mini_claude.tool_result import (
    MAX_TOOL_RESULT_BYTES,
    canonical_tool_result_bytes,
    public_tool_result,
    tool_result_byte_count,
    tool_result_char_count,
)
from mini_claude.tools import _read_file, execute_tool, tool_definitions


def _context() -> RunContext:
    return RunContext("session-boundary", "turn-boundary", "run-boundary", "invocation-boundary")


def test_common_limit_uses_normalized_json_utf8_bytes_and_has_exact_boundary():
    assert MAX_TOOL_RESULT_BYTES == 16 * 1024 * 1024
    exact = "x" * (MAX_TOOL_RESULT_BYTES - 2)  # JSON string quotes are counted.
    assert len(canonical_tool_result_bytes(exact)) == MAX_TOOL_RESULT_BYTES
    assert tool_result_byte_count("🙂" * 100) > tool_result_char_count("🙂" * 100)
    assert public_tool_result(exact, "fixture") == exact

    escaped = {"text": "中🙂\n\t\""}
    assert tool_result_byte_count(escaped) == len(canonical_tool_result_bytes(escaped))

    error = json.loads(public_tool_result("x" * (MAX_TOOL_RESULT_BYTES - 1), "read_file"))
    assert error["kind"] == "tool_result_error"
    assert error["error_type"] == "result_too_large"
    assert error["limit_bytes"] == MAX_TOOL_RESULT_BYTES
    assert error["actual_bytes"] == MAX_TOOL_RESULT_BYTES + 1
    assert "offset" in error["hint"] and "limit" in error["hint"]
    assert "x" * 100 not in json.dumps(error)


def test_binary_result_is_measured_after_base64_normalization():
    value = b"\xff" * 12_600_001
    assert len(value) < MAX_TOOL_RESULT_BYTES
    assert tool_result_byte_count(value) > MAX_TOOL_RESULT_BYTES

    error = json.loads(public_tool_result(value, "binary_fixture"))
    assert error["error_type"] == "result_too_large"
    assert error["actual_bytes"] > MAX_TOOL_RESULT_BYTES
    assert "data" not in error

    encoded = json.loads(public_tool_result(b"hello", "binary_fixture"))
    assert encoded == {
        "kind": "binary_tool_result",
        "encoding": "base64",
        "data": "aGVsbG8=",
    }


def test_non_serializable_result_is_bounded_without_string_fallback():
    value: list[object] = []
    value.append(value)

    error = json.loads(public_tool_result(value, "fixture"))
    assert error["error_type"] == "result_not_serializable"
    assert "0x" not in json.dumps(error)


def test_durable_boundary_rejects_binary_only_after_canonical_expansion(tmp_path: Path):
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts")
    context = _context()
    result = asyncio.run(
        DurableToolBoundary(
            RuntimeEventEmitter(sink),
            context,
            artifact_archive=archive,
            archive_capability=ToolResultArchiveCapability(
                archive, session_id=context.session_id, run_id=context.run_id
            ),
        ).execute(
            call_id="call-binary-too-large",
            name="fixture",
            arguments={},
            executor=lambda: b"\xff" * 12_600_001,
        )
    )

    assert result.success is False
    assert result.error_type == "result_too_large"
    assert result.result["actual_bytes"] > MAX_TOOL_RESULT_BYTES
    assert not list((tmp_path / "artifacts").rglob("*"))


def test_durable_boundary_reports_non_serializable_result_without_archiving(tmp_path: Path):
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts")
    context = _context()
    value: list[object] = []
    value.append(value)
    result = asyncio.run(
        DurableToolBoundary(
            RuntimeEventEmitter(sink),
            context,
            artifact_archive=archive,
            archive_capability=ToolResultArchiveCapability(
                archive, session_id=context.session_id, run_id=context.run_id
            ),
        ).execute(
            call_id="call-circular",
            name="fixture",
            arguments={},
            executor=lambda: value,
        )
    )

    assert result.success is False
    assert result.error_type == "result_not_serializable"
    assert result.result["error_type"] == "result_not_serializable"
    assert not list((tmp_path / "artifacts").rglob("*"))


def test_agent_passes_raw_structured_result_to_single_boundary(tmp_path: Path):
    async def scenario() -> None:
        sink = RecordingEventSink()
        context = _context()
        agent = Agent(
            api_key="fixture-key",
            model="fixture-model",
            thinking_effort="none",
            custom_system_prompt="fixture",
        )
        agent._runtime_context = context
        agent._runtime_emitter = RuntimeEventEmitter(sink)
        agent._runtime_boundary = DurableToolBoundary(
            agent._runtime_emitter, context
        )
        value = {"text": "x" * (MAX_TOOL_RESULT_BYTES - 12)}
        assert len(canonical_tool_result_bytes(value)) == MAX_TOOL_RESULT_BYTES - 1
        assert tool_result_byte_count(public_tool_result(value, "fixture")) > MAX_TOOL_RESULT_BYTES
        agent._archive_capability = SimpleNamespace(execute=lambda _inp: value)

        raw = await agent._execute_tool_call("ArchiveRead", {})
        assert raw is value
        result, success, executed = await agent._run_durable_tool(
            request_id="request-single-boundary",
            call_id="call-single-boundary",
            name="ArchiveRead",
            inp={},
            permission="allow",
        )

        assert success is True
        assert executed is True
        assert result == value

    asyncio.run(scenario())


def test_boundary_keeps_large_canonical_result_and_does_not_archive_early(tmp_path: Path):
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts")
    context = _context()
    value = "普通正文🙂\n" * 3_000
    result = asyncio.run(
        DurableToolBoundary(
            RuntimeEventEmitter(sink),
            context,
            max_result_bytes=32,
            artifact_archive=archive,
            archive_capability=ToolResultArchiveCapability(
                archive, session_id=context.session_id, run_id=context.run_id
            ),
        ).execute(
            call_id="call-large",
            name="read_file",
            arguments={"file_path": "sample.txt"},
            executor=lambda: value,
        )
    )

    assert result.success is True
    assert result.result == value
    assert not list((tmp_path / "artifacts").rglob("*.bin"))
    event = next(event for event in sink.events if event.kind == "function_response")
    assert event.content["result"] == value


def test_boundary_over_limit_is_controlled_and_has_no_archive_or_payload(tmp_path: Path):
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts")
    context = _context()
    result = asyncio.run(
        DurableToolBoundary(
            RuntimeEventEmitter(sink),
            context,
            artifact_archive=archive,
            archive_capability=ToolResultArchiveCapability(
                archive, session_id=context.session_id, run_id=context.run_id
            ),
        ).execute(
            call_id="call-too-large",
            name="read_file",
            arguments={"file_path": "sample.txt"},
            executor=lambda: "secret-body-" + ("x" * MAX_TOOL_RESULT_BYTES),
        )
    )

    assert result.success is False
    assert result.error_type == "result_too_large"
    assert result.result["error_type"] == "result_too_large"
    assert "secret-body" not in json.dumps(result.result)
    assert not list((tmp_path / "artifacts").rglob("*"))


def test_outcome_and_canonical_sink_redact_secrets_but_keep_nested_large_text():
    sink = RecordingEventSink()
    context = _context()
    value = {
        "ordinary": "x" * 20_000,
        "nested": {"text": "y" * 10_000},
        "api_key": "sk-ant-do-not-store",
    }
    result = asyncio.run(
        DurableToolBoundary(
            RuntimeEventEmitter(
                sink,
                redaction_policy=RedactionPolicy(max_string_chars=100),
            ),
            context,
        ).execute(
            call_id="call-redaction",
            name="fixture",
            arguments={},
            executor=lambda: value,
        )
    )

    assert result.success is True
    event = next(event for event in sink.events if event.kind == "function_response")
    stored = event.content["result"]
    assert stored["ordinary"] == value["ordinary"]
    assert stored["nested"]["text"] == value["nested"]["text"]
    assert stored["api_key"] == "[REDACTED]"
    assert "inline:" not in json.dumps(thaw(stored), ensure_ascii=False)

    direct = redact_payload(
        {"content": {"result": {"text": "z" * 100}}},
        RedactionPolicy(max_string_chars=8),
    )
    assert direct["content"]["result"]["text"] == "z" * 100


def test_read_file_pagination_preserves_original_line_numbers_and_strict_validation(tmp_path: Path):
    path = tmp_path / "lines.txt"
    path.write_text("一\r\n二\r\n三", encoding="utf-8", newline="")

    assert _read_file({"file_path": str(path)}) == "   1 | 一\n   2 | 二\n   3 | 三"
    assert _read_file({"file_path": str(path), "offset": 1, "limit": 1}) == "   2 | 二"
    assert _read_file({"file_path": str(path), "offset": 3, "limit": 2}) == ""
    for bad in (
        {"offset": -1},
        {"offset": True},
        {"offset": 1.0},
        {"offset": "1"},
        {"limit": 0},
        {"limit": False},
        {"limit": 1.0},
        {"limit": "1"},
    ):
        request = {"file_path": str(path), **bad}
        assert _read_file(request).startswith("Error: read_file")


def test_read_file_invalid_page_does_not_touch_mtime_state_or_archive(tmp_path: Path):
    path = tmp_path / "lines.txt"
    path.write_text("line", encoding="utf-8")
    state: dict[str, float] = {}
    result = asyncio.run(
        execute_tool(
            "read_file",
            {"file_path": str(path), "offset": 0, "limit": False},
            state,
        )
    )
    assert result.startswith("Error: read_file limit")
    assert state == {}


def test_read_file_schema_exposes_optional_pagination_fields():
    definition = next(item for item in tool_definitions if item["name"] == "read_file")
    properties = definition["input_schema"]["properties"]
    assert properties["offset"]["type"] == "integer"
    assert properties["limit"]["type"] == "integer"
    assert definition["input_schema"]["required"] == ["file_path"]


def test_read_file_page_over_limit_returns_error_instead_of_mixed_head_tail(tmp_path: Path):
    path = tmp_path / "huge.txt"
    path.write_text("x" * MAX_TOOL_RESULT_BYTES, encoding="utf-8")
    state: dict[str, float] = {}
    result = asyncio.run(
        execute_tool("read_file", {"file_path": str(path)}, state)
    )
    error = json.loads(result)
    assert error["error_type"] == "result_too_large"
    assert "truncated" not in result
    assert state == {}


def test_mcp_result_uses_the_same_common_public_limit():
    connection = McpConnection("fixture", "unused")

    async def fake_request(*args, **kwargs):
        del args, kwargs
        return {"content": [{"type": "text", "text": "m" * MAX_TOOL_RESULT_BYTES}]}

    connection._send_request = fake_request  # type: ignore[method-assign]
    error = json.loads(asyncio.run(connection.call_tool("large", {})))
    assert error["error_type"] == "result_too_large"
    assert error["tool_name"] == "large"


def test_mcp_structured_result_is_counted_as_canonical_json_bytes():
    connection = McpConnection("fixture", "unused")

    async def fake_request(*args, **kwargs):
        del args, kwargs
        return {"result": "🙂" * ((MAX_TOOL_RESULT_BYTES // 4) + 1)}

    connection._send_request = fake_request  # type: ignore[method-assign]
    error = json.loads(asyncio.run(connection.call_tool("structured", {})))
    assert error["error_type"] == "result_too_large"
    assert error["actual_bytes"] > MAX_TOOL_RESULT_BYTES
    assert "result" not in error
