from __future__ import annotations

import pytest

from mini_claude.agent import Agent
from mini_claude.compaction import CompactionCheckpointBuilder
from mini_claude.context_transition import build_context_transition
from mini_claude.event_ids import IdentityFactory, RunContext
from mini_claude.projections.base import EventRecord
from mini_claude.projections.incremental_replay import (
    IncrementalModelReplayCursor,
    IncrementalReplayError,
)
from mini_claude.projections.model_replay_projection import ModelReplayProjection
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore


def _opening(context: RunContext) -> RuntimeEvent:
    return RuntimeEvent.create(
        context,
        role="system",
        author="agent",
        content={
            "kind": "invocation_opened",
            "protocol": "invocation_opened_v1",
            "route": {"provider": "fixture"},
            "configuration": {"mode": "test"},
            "root": {"kind": "agent"},
            "source": {"kind": "fresh"},
        },
        metadata={"lifecycle": "invocation_opened"},
    )


def _text(context: RunContext, value: str) -> RuntimeEvent:
    return RuntimeEvent.create(
        context,
        role="user",
        author="user",
        content={"kind": "text", "text": value},
    )


def test_identity_factory_allocates_distinct_child_run_invocations_and_contexts():
    parent = RunContext(
        session_id="session-identity",
        turn_id="turn-1",
        run_id="run-parent",
        invocation_id="inv-parent",
    )
    factory = IdentityFactory(token_factory=iter(["run-a", "inv-a", "ctx-a", "run-b", "inv-b", "ctx-b"]).__next__)

    first = factory.child_context(parent)
    second = factory.child_context(parent)

    assert first.run_id != second.run_id
    assert first.invocation_id != second.invocation_id
    assert first.context_id != second.context_id
    assert first.parent_run_id == parent.run_id
    assert first.parent_context_id == parent.context_id


def test_shared_store_accepts_parent_and_children_and_scopes_model_projection(tmp_path):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        parent_context = RunContext(
            session_id="session-tree",
            turn_id="turn-1",
            run_id="run-parent",
            invocation_id="inv-parent",
        )
        child_one = parent_context.child(
            run_id="run-child-one", invocation_id="inv-child-one", context_id="context:child-one"
        )
        child_two = parent_context.child(
            run_id="run-child-two", invocation_id="inv-child-two", context_id="context:child-two"
        )
        for event in (
            _opening(parent_context),
            _text(parent_context, "parent"),
            _opening(child_one),
            _text(child_one, "child one"),
            _opening(child_two),
            _text(child_two, "child two"),
        ):
            store.append(event)

        parent = ModelReplayProjection().build(
            store, context_id=parent_context.context_id
        )
        one = ModelReplayProjection().build(store, context_id=child_one.context_id)
        two = ModelReplayProjection().build(store, context_id=child_two.context_id)

        assert [item["content"] for item in parent.messages] == ["parent"]
        assert [item["content"] for item in one.messages] == ["child one"]
        assert [item["content"] for item in two.messages] == ["child two"]
        assert store.read_event_records(context_id=parent_context.context_id)[-1][0] == 2


def test_agent_child_startup_uses_fresh_invocation_ids(tmp_path):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        parent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
            runtime_session_id="session-agent-tree",
            runtime_run_id="run-parent",
            runtime_context_id="context:session-agent-tree",
        )
        parent._ask_count = 1
        parent._setup_runtime_facade()

        child_kwargs = {
            "api_base": "https://fake-provider.invalid/v1",
            "api_key": "fixture-key",
            "is_sub_agent": True,
            "runtime_store": store,
            "runtime_session_id": "session-agent-tree",
            "runtime_parent_run_id": parent._runtime_context.run_id,
            "runtime_parent_context_id": parent._runtime_context.context_id,
        }
        children = [
            Agent(**child_kwargs, runtime_run_id=f"run-child-{index}", runtime_context_id=f"context:child-{index}")
            for index in (1, 2)
        ]
        for child in children:
            child._ask_count = 1
            child._setup_runtime_facade()

        events = [event for _, event in store.read_event_records()]
        openings = [event for event in events if event.kind == "invocation_opened"]
        assert len(openings) == 3
        assert len({event.invocation_id for event in openings}) == 3
        assert len({event.context_id for event in openings}) == 3
        assert all(event.parent_run_id == parent._runtime_context.run_id for event in openings[1:])


def test_cursor_rejects_events_from_a_foreign_context():
    first = RunContext(
        session_id="session-cursor",
        turn_id="turn-1",
        run_id="run-one",
        invocation_id="inv-one",
        context_id="context:one",
    )
    second = first.child(
        run_id="run-two", invocation_id="inv-two", context_id="context:two"
    )
    cursor = IncrementalModelReplayCursor(context_id=first.context_id)
    cursor.append([EventRecord(1, _opening(first))])

    with pytest.raises(IncrementalReplayError, match="does not match cursor"):
        cursor.append([EventRecord(2, _opening(second))])


def test_child_compaction_does_not_replace_parent_model_context(tmp_path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        parent = RunContext(
            session_id="session-compaction-tree",
            turn_id="turn-1",
            run_id="run-parent",
            invocation_id="inv-parent",
            context_id="context:parent",
        )
        child = parent.child(
            run_id="run-child", invocation_id="inv-child", context_id="context:child"
        )
        for event in (
            _opening(parent),
            _text(parent, "parent stays visible"),
            _opening(child),
            _text(child, "child detail"),
            RuntimeEvent.create(
                child,
                role="model",
                author="agent",
                content={
                    "kind": "function_call",
                    "id": "child-call",
                    "name": "read_file",
                    "args": {},
                },
            ),
            RuntimeEvent.create(
                child,
                role="tool",
                author="tool",
                content={
                    "kind": "function_response",
                    "id": "child-call",
                    "name": "read_file",
                    "result": "child result",
                },
            ),
        ):
            store.append(event)

        high_water = store.current_high_water
        child_projection = ModelReplayProjection().build(
            store, high_water=high_water, context_id=child.context_id
        )
        retained = [dict(message) for message in child_projection.messages[-2:]]
        context_messages = [
            {"role": "user", "content": "child summary"},
            *retained,
        ]
        checkpoint = CompactionCheckpointBuilder().build(
            store, high_water=high_water, context_id=child.context_id
        )
        transition = build_context_transition(
            source_high_water=high_water,
            source_digest=checkpoint.source_digest,
            projection_version=checkpoint.projection_version,
            policy_version="compression-policy-v1",
            context_epoch="context:child-compacted",
            reason="full_compaction",
            replacements=[],
            effective_context=context_messages,
            context_id=child.context_id,
        )
        activation = RuntimeEvent.create(
            child,
            role="system",
            author="system",
            actions={
                "context_transition": transition.to_dict(),
                "compaction": {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "source_high_water": high_water,
                    "source_digest": checkpoint.source_digest,
                    "reset_model_context": True,
                    "context_messages": context_messages,
                },
            },
        )
        store.append_compaction_transition(checkpoint, activation)

        parent_projection = ModelReplayProjection().build(
            store, context_id=parent.context_id
        )
        child_projection = ModelReplayProjection().build(
            store, context_id=child.context_id
        )
        assert [message["content"] for message in parent_projection.messages] == [
            "parent stays visible"
        ]
        assert child_projection.messages[0]["content"] == "child summary"


def test_restore_rebinds_root_context_to_resumed_session(tmp_path):
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        context = RunContext(
            session_id="session-restored",
            turn_id="turn-1",
            run_id="run-existing",
            invocation_id="inv-existing",
        )
        store.append(_opening(context))
        store.append(_text(context, "resumed history"))
        agent = Agent(
            api_base="https://fake-provider.invalid/v1",
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
            runtime_session_id="session-old",
        )
        agent.restore_session(
            {
                "source": "canonical",
                "metadata": {"id": "session-restored", "askCount": 1},
                "canonicalMessages": [],
            }
        )
        agent._setup_runtime_facade()
        assert agent._runtime_context is not None
        assert agent._runtime_context.context_id == "context:session-restored"
        restored = agent._refresh_provider_context_from_canonical()
        assert restored.messages[-1]["content"] == "resumed history"
