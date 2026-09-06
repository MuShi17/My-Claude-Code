"""C06 provider and durable tool-boundary contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mini_claude.event_ids import IdentityFactory, RunContext
from mini_claude.event_sink import CanonicalSink, CanonicalSinkError, RecordingEventSink, RuntimeEventEmitter
from mini_claude.runtime_lifecycle import (
    DurableToolBoundary,
    ModelCallRecorder,
    ToolOperationConflictError,
    UncertainToolOperationError,
    decode_tool_arguments,
    request_shape_hash,
)
from mini_claude.runtime_store import SQLiteRuntimeStore


def _context() -> RunContext:
    return RunContext("session-c06", "turn-c06", "run-c06", "invocation-c06")


def _emitter(*, failure_hook=None) -> tuple[RuntimeEventEmitter, RecordingEventSink]:
    sink = RecordingEventSink(failure_hook=failure_hook)
    return RuntimeEventEmitter(sink), sink


def test_model_recorder_has_provider_neutral_lifecycle_and_unknown_usage():
    emitter, sink = _emitter()
    ids = IdentityFactory(token_factory=iter(["1", "2", "3", "4", "5", "6", "7"]).__next__)
    recorder = ModelCallRecorder(
        emitter,
        _context(),
        provider="anthropic",
        model="fixture-model",
        id_factory=ids,
        clock=lambda: 1_700_000_000_000,
    )
    recorder.start("request-1", attempt=2, request={"model": "fixture-model", "messages": []})
    recorder.partial_text("hello")
    recorder.partial_tool_arguments("call-1", "read_file", '{"file')
    recorder.final_text("hello world")
    summary = recorder.finish("stop", usage={"input_tokens": 10, "output_tokens": 4})
    assert summary.attempt == 2
    assert [event.metadata["provider"] for event in sink.events] == ["anthropic"] * len(sink.events)
    assert any(event.partial for event in sink.events)
    usage = next(event for event in sink.events if event.kind == "usage")
    assert usage.actions["usage"]["input_tokens"] == 10
    assert "request_shape_hash" in sink.events[0].metadata

    emitter2, sink2 = _emitter()
    recorder2 = ModelCallRecorder(emitter2, _context(), provider="openai", model="fixture-model")
    recorder2.start("request-2")
    recorder2.finish("stop", usage=None)
    usage2 = next(event for event in sink2.events if event.kind == "usage")
    assert usage2.actions["usage"] == {}
    assert usage2.metadata["usage_status"] == "unknown"


def test_request_hash_and_final_argument_decoder_are_deterministic():
    assert request_shape_hash({"b": 2, "a": 1}) == request_shape_hash({"a": 1, "b": 2})
    value, error = decode_tool_arguments('{"file_path":"sample.txt"}')
    assert value == {"file_path": "sample.txt"}
    assert error is None
    value, error = decode_tool_arguments('{"file_path":')
    assert value == '{"file_path":'
    assert error and error.startswith("invalid_json:")


def test_model_retry_has_distinct_attempt_identity_and_summary_link():
    emitter, sink = _emitter()
    recorder = ModelCallRecorder(emitter, _context(), provider="anthropic", model="fixture-model")
    recorder.start("request-retry")
    first_attempt_id = recorder.attempt_id
    retry_event = recorder.retry(reason="overloaded", attempt=2)
    second_attempt_id = recorder.attempt_id
    summary = recorder.finish("stop", usage={"input_tokens": 1, "output_tokens": 1})

    assert first_attempt_id
    assert second_attempt_id and second_attempt_id != first_attempt_id
    assert retry_event.actions["attempt_retry"]["attempt_id"] == second_attempt_id
    assert retry_event.actions["attempt_retry"]["previous_attempt_id"] == first_attempt_id
    assert all(event.metadata["attempt_id"] for event in sink.events)
    assert summary.attempt_id == second_attempt_id


def test_permission_denial_has_no_side_effect_or_executed_outcome():
    async def scenario():
        emitter, sink = _emitter()
        boundary = DurableToolBoundary(emitter, _context())
        calls: list[str] = []
        result = await boundary.execute(
            call_id="call-denied",
            name="run_shell",
            arguments={"command": "touch marker"},
            permission={"decision": "deny", "reason": "policy"},
            executor=lambda: calls.append("executed"),
        )
        assert result.executed is False
        assert calls == []
        outcome = next(event for event in sink.events if event.kind == "tool_outcome")
        assert outcome.actions["tool_outcome"]["executed"] is False

    asyncio.run(scenario())


def test_dispatch_must_durable_before_executor():
    async def scenario():
        order: list[str] = []

        def fail(point: str, event):
            if point == "emit" and event.metadata.get("lifecycle") == "tool_dispatch":
                order.append("dispatch_failed")
                raise OSError("store unavailable")

        emitter, _ = _emitter(failure_hook=fail)
        boundary = DurableToolBoundary(emitter, _context())
        with pytest.raises(CanonicalSinkError, match="store unavailable"):
            await boundary.execute(
                call_id="call-1",
                name="write_file",
                arguments={"file_path": "x", "content": "data"},
                permission="allow",
                executor=lambda: order.append("executed"),
            )
        assert order == ["dispatch_failed"]
        assert boundary.execution_count == 0

    asyncio.run(scenario())


def test_success_and_invalid_final_arguments_have_distinct_terminal_outcomes():
    async def scenario():
        emitter, sink = _emitter()
        boundary = DurableToolBoundary(emitter, _context())
        result = await boundary.execute(
            call_id="call-ok",
            name="read_file",
            arguments='{"file_path":"sample.txt"}',
            permission="allow",
            executor=lambda: "alpha",
        )
        assert result.executed is True
        kinds = [event.kind for event in sink.events]
        assert kinds[:3] == ["function_call", "permission", "tool_dispatch"]
        assert kinds[-2:] == ["tool_outcome", "function_response"]

        result = await boundary.execute(
            call_id="call-invalid",
            name="read_file",
            arguments='{"file_path":',
            permission="allow",
            executor=lambda: (_ for _ in ()).throw(AssertionError("must not execute")),
        )
        assert result.executed is False
        assert result.error_type == "ValidationError"

    asyncio.run(scenario())


def test_sqlite_tool_operation_is_durable_idempotent_and_correlatable(tmp_path: Path):
    async def scenario():
        database = tmp_path / "runtime.sqlite"
        with SQLiteRuntimeStore(database) as store:
            emitter = RuntimeEventEmitter(store)
            recorder = ModelCallRecorder(
                emitter, _context(), provider="fixture", model="fixture-model"
            )
            recorder.start("request-tool")
            boundary = DurableToolBoundary(emitter, _context())
            calls: list[str] = []
            first = await boundary.execute(
                call_id="provider-call-1",
                name="read_file",
                arguments={"file_path": "sample.txt"},
                executor=lambda: calls.append("executed") or "alpha",
            )
            second = await boundary.execute(
                call_id="provider-call-1",
                name="read_file",
                arguments={"file_path": "sample.txt"},
                executor=lambda: calls.append("replayed") or "wrong",
            )
            operation = store.read_tool_operation(first.operation_id or "")
            assert operation is not None
            assert operation.state == "completed"
            assert operation.provider_tool_call_id == "provider-call-1"
            assert operation.canonical_args_hash.startswith("sha256:")
            assert first.result == second.result == "alpha"
            assert calls == ["executed"]
            assert len(store.connection.execute(
                "SELECT journal_id FROM runtime_tool_journal WHERE operation_id = ?",
                (first.operation_id,),
            ).fetchall()) == 2

    asyncio.run(scenario())


def test_sqlite_tool_operation_conflict_and_unknown_are_fail_closed(tmp_path: Path):
    async def scenario():
        database = tmp_path / "runtime.sqlite"
        with SQLiteRuntimeStore(database) as store:
            emitter = RuntimeEventEmitter(store)
            recorder = ModelCallRecorder(
                emitter, _context(), provider="fixture", model="fixture-model"
            )
            recorder.start("request-tool")
            boundary = DurableToolBoundary(emitter, _context())
            await boundary.execute(
                call_id="provider-call-1",
                name="read_file",
                arguments={"file_path": "sample.txt"},
                executor=lambda: "alpha",
            )
            with pytest.raises(ToolOperationConflictError):
                await boundary.execute(
                    call_id="provider-call-1",
                    name="read_file",
                    arguments={"file_path": "other.txt"},
                    executor=lambda: pytest.fail("conflicting operation must not execute"),
                )

            store.connection.execute(
                "UPDATE runtime_tool_operations SET state = 'dispatched', "
                "outcome_event_id = NULL, success = NULL, executed = NULL, result_json = NULL, "
                "result_digest = NULL, result_size_bytes = NULL WHERE provider_tool_call_id = ?",
                ("provider-call-1",),
            )
            assert store.mark_unknown_tool_operations(run_id="run-c06") == 1
            with pytest.raises(UncertainToolOperationError):
                await boundary.execute(
                    call_id="provider-call-1",
                    name="read_file",
                    arguments={"file_path": "sample.txt"},
                    executor=lambda: pytest.fail("unknown operation must not execute"),
                )

    asyncio.run(scenario())


def test_dispatch_store_failure_does_not_invoke_executor(tmp_path: Path):
    async def scenario():
        database = tmp_path / "runtime.sqlite"
        with SQLiteRuntimeStore(database) as store:
            emitter = RuntimeEventEmitter(store)
            recorder = ModelCallRecorder(
                emitter, _context(), provider="fixture", model="fixture-model"
            )
            recorder.start("request-tool")
            class CommitFault:
                commits = 0

                def check(self, point: str) -> None:
                    if point == "store.append":
                        self.commits += 1
                        if self.commits == 3:
                            raise RuntimeError("dispatch persistence failed")

            store.fault_hook = CommitFault()
            boundary = DurableToolBoundary(emitter, _context())
            calls: list[str] = []
            with pytest.raises(Exception, match="dispatch persistence failed"):
                await boundary.execute(
                    call_id="provider-call-1",
                    name="write_file",
                    arguments={"file_path": "sample.txt", "content": "x"},
                    executor=lambda: calls.append("executed"),
                )
            assert calls == []

    asyncio.run(scenario())
