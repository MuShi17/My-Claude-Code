"""Integration regressions for canonical runtime remediation changes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_claude.agent import (
    Agent,
    CanonicalFinalizationError,
    ProviderContentNormalizationError,
)
from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.compaction import CompactionCheckpointBuilder, CompactionError
from mini_claude.event_ids import RunContext
from mini_claude.event_sink import RecordingEventSink
from mini_claude.llm_capture import LLMCapturePolicy
from mini_claude.provider_content import materialize_tool_result
from mini_claude.projections import CanonicalModelContextAdapter
from mini_claude.projections import ModelReplayProjection
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore

from runtime_fixtures import DeterministicIdFactory, build_scenario, scenario_events


def _anthropic_response(*blocks, input_tokens: int = 10, output_tokens: int = 8):
    return SimpleNamespace(
        content=list(blocks),
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def _anthropic_block(block_type: str, **fields):
    return SimpleNamespace(type=block_type, **fields)


def _capture_once(
    tmp_path: Path, policy: LLMCapturePolicy, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict, Path]:
    del monkeypatch
    session_id = "integration-capture"
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
        agent = Agent(
            runtime_store=store,
            runtime_sink=RecordingEventSink(),
            artifact_archive=archive,
            llm_capture_policy=policy,
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        agent._capture_llm(
            request_id="req-integration",
            messages=[
                {"role": "user", "content": "password=sk-ant-integration-secret"}
            ],
            response={"content": "response-secret"},
            usage={"input_tokens": 1, "output_tokens": 1},
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=None,
            finish_reason="stop",
        )
        capture = store.read_llm_capture("llm:req-integration:attempt:1")
    assert capture is not None
    return capture, tmp_path / "artifacts"


def test_agent_capture_off_never_writes_legacy_raw_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    capture, artifacts = _capture_once(tmp_path, LLMCapturePolicy(mode="off"), monkeypatch)

    assert capture["capture_status"] == "off"
    assert capture["metadata"]["body_present"] is False
    assert not list(artifacts.rglob("*"))
    assert "sk-ant-integration-secret" not in json.dumps(capture)


def test_agent_metadata_only_keeps_legacy_shadow_body_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    capture, artifacts = _capture_once(
        tmp_path, LLMCapturePolicy(mode="metadata-only"), monkeypatch
    )

    assert capture["capture_status"] == "metadata-only"
    assert capture["metadata"]["body_present"] is False
    assert not list(artifacts.rglob("*"))
    assert "sk-ant-integration-secret" not in json.dumps(capture)


def test_agent_redacted_capture_does_not_call_legacy_raw_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    capture, artifacts = _capture_once(
        tmp_path,
        LLMCapturePolicy(mode="redacted", max_body_bytes=128, archive_bodies=True),
        monkeypatch,
    )

    assert capture["capture_status"] == "saved"
    assert capture["llm_ref"].startswith("llm:req-integration")
    assert capture["metadata"]["body_present"] is True
    archived = list(artifacts.rglob("*.bin"))
    assert archived
    assert all("sk-ant-integration-secret" not in path.read_text(encoding="utf-8", errors="replace") for path in archived)


@pytest.mark.parametrize("api_base", [None, "https://fake-provider.invalid/v1"])
def test_chat_emits_original_user_event_for_both_provider_loops(
    api_base: str | None, monkeypatch: pytest.MonkeyPatch
):
    sink = RecordingEventSink()

    async def fake_loop(self: Agent, user_message: str) -> None:
        assert user_message == "hello from user"

    if api_base is None:
        monkeypatch.setattr(Agent, "_chat_anthropic", fake_loop)
    else:
        monkeypatch.setattr(Agent, "_chat_openai", fake_loop)
    agent = Agent(
        api_base=api_base,
        api_key="fixture-key",
        is_sub_agent=True,
        runtime_sink=sink,
    )
    asyncio.run(agent.chat("hello from user"))

    user_events = [event for event in sink.events if event.role == "user"]
    assert len(user_events) == 1
    assert user_events[0].content["text"] == "hello from user"
    assert user_events[0].metadata["lifecycle"] == "user_input"
    assert any(event.is_terminal for event in sink.events)


def test_anthropic_malformed_thinking_fails_before_tool_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    response = _anthropic_response(
        _anthropic_block("tool_use", id="call-1", name="read_file", input={"path": "secret"}),
        _anthropic_block(
            "thinking",
            thinking={"secret": "must not enter canonical content"},
            signature="sig-1",
        ),
    )

    async def fake_call(self: Agent):
        return response

    async def unexpected_tool(*args, **kwargs):
        raise AssertionError("malformed provider content must fail before tool dispatch")

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    monkeypatch.setattr(Agent, "_run_durable_tool", unexpected_tool)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            model="deepseek-v4-flash",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )

        with pytest.raises(ProviderContentNormalizationError) as error:
            asyncio.run(agent.chat("inspect safely"))

        events = [event for _, event in store.read_event_records()]
        assert error.value.block_kind == "thinking"
        assert error.value.block_index == 1
        assert error.value.value_type == "mapping"
        assert "must not enter canonical content" not in str(error.value)
        assert not any(
            event.role == "model"
            and event.content
            and event.content.get("kind") in {"text", "thinking", "function_call"}
            for event in events
        )
        failed = [event for event in events if event.status == "failed"]
        assert len(failed) == 1
        assert failed[0].content["code"] == "provider_content_normalization_failed"
        assert failed[0].content["message"] == str(error.value)
        assert agent._runtime_exit_status == "failed"


def test_openai_malformed_text_fails_before_message_history_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    response = {
        "choices": [{
            "message": {
                "content": {"secret": "must not enter openai history"},
                "tool_calls": [],
            }
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    async def fake_call(self: Agent):
        return response

    monkeypatch.setattr(Agent, "_call_openai_stream", fake_call)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )

        with pytest.raises(ProviderContentNormalizationError) as error:
            asyncio.run(agent.chat("inspect safely"))

        assert error.value.provider == "openai"
        assert error.value.block_kind == "text"
        assert error.value.value_type == "mapping"
        assert "must not enter openai history" not in str(error.value)
        assert not any(
            message.get("content") == {"secret": "must not enter openai history"}
            for message in agent._openai_messages
        )
        assert any(event.status == "failed" for _, event in store.read_event_records())


def test_openai_reasoning_is_canonical_and_replayable_with_tool_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    responses = iter([
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "inspect the file",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({
                                "file_path": str(tmp_path / "sample.txt")
                            }),
                        },
                    }],
                }
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "inspection complete",
                    "reasoning_content": "",
                    "tool_calls": [],
                }
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    ])
    (tmp_path / "sample.txt").write_text("safe", encoding="utf-8")

    async def fake_call(self: Agent):
        return next(responses)

    monkeypatch.setattr(Agent, "_call_openai_stream", fake_call)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            model="deepseek-v4-flash",
            is_sub_agent=True,
            permission_mode="bypassPermissions",
            runtime_store=store,
        )
        asyncio.run(agent.chat("inspect the file"))

        context = CanonicalModelContextAdapter().build(
            store, provider="openai", system_prompt="system fixture"
        )
        tool_step = next(message for message in context.messages if message.get("tool_calls"))
        assert tool_step["reasoning_content"] == "inspect the file"
        assert tool_step["content"] == ""
        assert any(
            message.get("role") == "tool"
            and message.get("tool_call_id") == "call-1"
            for message in context.messages
        )
        assert any(
            message.get("content") == "inspection complete"
            and message.get("reasoning_content") == ""
            for message in context.messages
        )


def test_valid_anthropic_multi_tool_response_preserves_signature_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    responses = iter([
        _anthropic_response(
            _anthropic_block("thinking", thinking="reasoning", signature="sig-1"),
            _anthropic_block("text", text="I will inspect both files."),
            _anthropic_block(
                "tool_use", id="call-1", name="read_file", input={"file_path": str(tmp_path / "one.txt")}
            ),
            _anthropic_block(
                "tool_use", id="call-2", name="read_file", input={"file_path": str(tmp_path / "two.txt")}
            ),
        ),
        _anthropic_response(_anthropic_block("text", text="Inspection complete.")),
    ])
    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    (tmp_path / "two.txt").write_text("two", encoding="utf-8")

    async def fake_call(self: Agent):
        return next(responses)

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        agent = Agent(
            model="deepseek-v4-flash",
            api_key="fixture-key",
            is_sub_agent=True,
            permission_mode="bypassPermissions",
            runtime_store=store,
            artifact_archive=ArtifactArchive(tmp_path / "artifacts", metadata_store=store),
        )
        asyncio.run(agent.chat("inspect both files"))

        events = [event for _, event in store.read_event_records()]
        thinking = [
            event for event in events
            if event.content and event.content.get("kind") == "thinking"
        ]
        calls = [
            event for event in events
            if event.content and event.content.get("kind") == "function_call"
        ]
        assert [(event.content["text"], event.content["signature"]) for event in thinking] == [
            ("reasoning", "sig-1")
        ]
        assert list(dict.fromkeys(event.content["id"] for event in calls)) == ["call-1", "call-2"]

        context = CanonicalModelContextAdapter().build(store, provider="anthropic")
        tool_uses = [
            block
            for message in context.messages
            if message.get("role") == "assistant" and isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_use"
        ]
        tool_results = [
            block
            for message in context.messages
            if message.get("role") == "user" and isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        assert [block["id"] for block in tool_uses] == ["call-1", "call-2"]
        assert [block["tool_use_id"] for block in tool_results] == ["call-1", "call-2"]
        assert any(
            block.get("type") == "thinking" and block.get("signature") == "sig-1"
            for message in context.messages
            if message.get("role") == "assistant" and isinstance(message.get("content"), list)
            for block in message["content"]
        )


def test_anthropic_tool_end_counts_serialized_structured_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    responses = iter([
        _anthropic_response(
            _anthropic_block(
                "tool_use", id="call-large", name="read_file",
                input={"file_path": str(tmp_path / "interp.py")},
            )
        ),
        _anthropic_response(_anthropic_block("text", text="Done.")),
    ])
    bounded_result = {
        "kind": "bounded_ref",
        "ref": "artifact:sha256:" + "a" * 64,
        "size_bytes": 100_000,
        "inline": "preview",
        "truncated": True,
    }
    tool_end_events: list[dict] = []

    async def fake_call(self: Agent):
        return next(responses)

    async def fake_tool(self: Agent, **kwargs):
        return bounded_result, True, True

    monkeypatch.setattr(Agent, "_call_anthropic_stream", fake_call)
    monkeypatch.setattr(Agent, "_run_durable_tool", fake_tool)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            model="deepseek-v4-flash",
            api_key="fixture-key",
            is_sub_agent=True,
            permission_mode="bypassPermissions",
            runtime_store=store,
        )
        agent.on("tool_end", tool_end_events.append)

        asyncio.run(agent.chat("read the large file"))

    expected = materialize_tool_result(bounded_result, provider="anthropic")
    assert isinstance(expected, str)
    assert len(tool_end_events) == 1
    assert tool_end_events[0]["result_length"] == len(expected.encode("utf-8"))


def test_openai_serial_tool_end_counts_serialized_structured_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    responses = iter([
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call-serial",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }],
                }
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Done.",
                    "reasoning_content": "",
                    "tool_calls": [],
                }
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    ])
    bounded_result = {
        "kind": "bounded_ref",
        "ref": "artifact:sha256:" + "b" * 64,
        "size_bytes": 100_000,
        "inline": "preview",
        "truncated": True,
    }
    tool_end_events: list[dict] = []

    async def fake_call(self: Agent):
        return next(responses)

    async def fake_tool(self: Agent, **kwargs):
        return bounded_result, True, True

    monkeypatch.setattr(Agent, "_call_openai_stream", fake_call)
    monkeypatch.setattr(Agent, "_run_durable_tool", fake_tool)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            model="deepseek-v4-flash",
            is_sub_agent=True,
            permission_mode="bypassPermissions",
            runtime_store=store,
        )
        agent.on("tool_end", tool_end_events.append)

        asyncio.run(agent.chat("run the serial tool"))

    expected = materialize_tool_result(bounded_result, provider="openai")
    assert isinstance(expected, str)
    assert len(tool_end_events) == 1
    assert tool_end_events[0]["result_length"] == len(expected.encode("utf-8"))


def test_provider_context_adapter_preserves_tool_protocol_for_both_providers(tmp_path: Path):
    events = [
        RuntimeEvent.from_dict(item)
        for item in scenario_events(
            build_scenario(), ids=DeterministicIdFactory("adapter"), provider="fixture"
        )
    ]
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        for event in events:
            store.append(event)

        anthropic = CanonicalModelContextAdapter().build(store, provider="anthropic")
        openai = CanonicalModelContextAdapter().build(
            store, provider="openai", system_prompt="system fixture"
        )

    assert anthropic.high_water == openai.high_water == len(events)
    assert anthropic.source_digest == openai.source_digest
    assert anthropic.messages[0]["role"] == "assistant"
    assert anthropic.messages[0]["content"] == "I will read the file."
    assert anthropic.messages[1]["content"][0]["type"] == "tool_use"
    assert openai.messages[0] == {"role": "system", "content": "system fixture"}
    assert openai.messages[1]["role"] == "assistant"
    assert openai.messages[2]["tool_calls"][0]["name"] == "read_file"
    assert openai.messages[3]["role"] == "tool"
    assert all("runtime_event_id" not in message for message in anthropic.messages)


@pytest.mark.parametrize("api_base", [None, "https://fake-provider.invalid/v1"])
def test_provider_loop_refreshes_stale_arrays_from_canonical_store(
    tmp_path: Path, api_base: str | None
):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            api_base=api_base,
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        agent._emit_canonical_user_event("canonical user")
        if api_base is None:
            agent._anthropic_messages = [{"role": "user", "content": "stale legacy"}]
        else:
            agent._openai_messages = [
                {"role": "system", "content": "stale system"},
                {"role": "user", "content": "stale legacy"},
            ]

        context = agent._refresh_provider_context_from_canonical()

        assert context is not None
        messages = agent._openai_messages if api_base else agent._anthropic_messages
        assert messages[-1]["content"] == "canonical user"
        assert all("stale" not in str(message) for message in messages)
        if api_base:
            assert messages[0] == {"role": "system", "content": agent._system_prompt}


def test_real_agent_compaction_writes_checkpoint_and_replays_compacted_context(
    tmp_path: Path,
):
    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="bounded summary"))]
            )

    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        agent._emit_canonical_user_event("original user")
        agent._openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        agent._openai_messages = [
            {"role": "system", "content": agent._system_prompt},
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old answer"},
            {"role": "assistant", "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call-1", "content": "old result"},
        ]

        asyncio.run(agent._compact_conversation())

        checkpoints = store.list_compaction_checkpoints()
        assert len(checkpoints) == 1
        checkpoint = checkpoints[0]
        assert checkpoint["source_high_water"] == 3
        assert checkpoint["summary"]["text"] == "bounded summary"
        checkpoint_object = CompactionCheckpointBuilder().build(
            store, high_water=checkpoint["source_high_water"],
            checkpoint_id=checkpoint["checkpoint_id"],
            coverage=checkpoint["coverage"],
            summary=checkpoint["summary"],
            created_at=checkpoint["created_at"],
        )
        CompactionCheckpointBuilder().verify(checkpoint_object, store)

        replay = ModelReplayProjection().build(store)
        assert replay.messages[0]["content"].startswith("[Previous conversation summary]")
        assert replay.messages[-1]["role"] == "tool"

        later = RuntimeEvent.from_dict(
                {
                    "schema_version": 2,
                    "id": "later-event",
                    "ts": "2026-09-05T00:00:00Z",
                    "partial": False,
                    "session_id": agent.session_id,
                "turn_id": agent._runtime_context.turn_id,
                "run_id": agent._runtime_context.run_id,
                "invocation_id": agent._runtime_context.invocation_id,
                "role": "user",
                "author": "user",
                "content": {"kind": "text", "text": "later"},
            }
        )
        store.append(later)
        CompactionCheckpointBuilder().verify(checkpoint_object, store)

    with SQLiteRuntimeStore(database) as reopened:
        persisted = reopened.read_compaction_checkpoint(checkpoint["checkpoint_id"])
        assert persisted is not None
        replay = ModelReplayProjection().build(reopened)
        assert replay.messages[-1]["content"] == "later"


def test_compaction_checkpoint_failure_preserves_canonical_prefix_without_ref(
    tmp_path: Path,
):
    class FailingStore(SQLiteRuntimeStore):
        def append_compaction_transition(self, checkpoint, event):
            raise RuntimeError("checkpoint fsync failed")

    with FailingStore(tmp_path / "runtime.sqlite") as store:
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        agent._emit_canonical_user_event("prefix survives")
        with pytest.raises(CompactionError, match="checkpoint fsync failed"):
            agent._write_compaction_checkpoint("summary")
        assert store.current_high_water == 3
        assert store.list_compaction_checkpoints() == []
        assert not any(
            event.refs and "checkpoint_id" in event.refs
            for _, event in store.read_event_records()
        )


def test_canonical_terminal_finalize_failure_is_returned_as_controlled_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_terminal(operation, event):
        if operation == "emit" and event.status in {
            "completed", "failed", "cancelled", "aborted", "budget_exceeded"
        }:
            raise RuntimeError("seal unavailable")

    sink = RecordingEventSink(failure_hook=fail_terminal)

    async def fake_loop(self: Agent, user_message: str) -> None:
        assert user_message == "finish me"

    monkeypatch.setattr(Agent, "_chat_anthropic", fake_loop)
    agent = Agent(is_sub_agent=True, runtime_sink=sink)

    with pytest.raises(CanonicalFinalizationError, match="seal unavailable"):
        asyncio.run(agent.chat("finish me"))
    assert not any(event.is_terminal for event in sink.events)
    assert agent._runtime_exit_status == "failed"


def test_provider_error_remains_primary_when_canonical_finalize_also_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    def fail_terminal(operation, event):
        if operation == "emit" and event.status in {
            "completed", "failed", "cancelled", "aborted", "budget_exceeded"
        }:
            raise RuntimeError("seal unavailable")

    async def fake_loop(self: Agent, user_message: str) -> None:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(Agent, "_chat_anthropic", fake_loop)
    agent = Agent(
        is_sub_agent=True,
        runtime_sink=RecordingEventSink(failure_hook=fail_terminal),
    )

    with pytest.raises(RuntimeError, match="provider exploded") as error:
        asyncio.run(agent.chat("provider failure"))
    assert any("canonical finalization failed" in note for note in error.value.__notes__)


def test_child_agent_inherits_runtime_policy_and_parent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    captured: list[Agent] = []

    async def fake_run_once(self: Agent, prompt: str) -> dict:
        captured.append(self)
        return {"text": "child result", "tokens": {"input": 0, "output": 0}}

    monkeypatch.setattr(Agent, "run_once", fake_run_once)
    policy = LLMCapturePolicy(mode="redacted", max_body_bytes=123)
    sink = RecordingEventSink()
    archive = ArtifactArchive(tmp_path / "artifacts")
    parent = Agent(
        is_sub_agent=True,
        runtime_store=None,
        runtime_sink=sink,
        artifact_archive=archive,
        llm_capture_policy=policy,
    )
    parent._runtime_context = RunContext("session-parent", "turn-parent", "run-parent", "inv-parent")

    result = asyncio.run(parent._execute_agent_tool({
        "type": "explore", "description": "inspect", "prompt": "look around"
    }))

    assert result == "child result"
    assert len(captured) == 1
    child = captured[0]
    assert child._runtime_store is parent._runtime_store
    assert child._runtime_sink is parent._runtime_sink
    assert child._artifact_archive is parent._artifact_archive
    assert child._llm_capture_policy is policy
    assert child.session_id == "session-parent"
    assert child._runtime_parent_run_id == "run-parent"
    assert child._runtime_run_id.startswith("run-")
