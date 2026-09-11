"""Regression tests for session-level runtime and artifact lifecycles."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rollo.agent import (
    Agent,
    AgentClosedError,
    RuntimeResourceMismatchError,
)
from rollo.artifact_archive import ArtifactArchive, ArtifactMetadataError
from rollo.runtime_lifecycle import DurableToolBoundary
from rollo.runtime_store import SQLiteRuntimeStore, StoreClosedError


def _patch_agent_runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rollo.agent.runtime_store_path",
        lambda session_id: tmp_path / "runtime.sqlite",
    )
    monkeypatch.setattr(
        "rollo.agent.runtime_data_dir",
        lambda: tmp_path,
    )


def _install_noop_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(self: Agent, user_message: str) -> None:
        del self, user_message

    monkeypatch.setattr(Agent, "_chat_anthropic", fake_loop)


def test_owned_runtime_and_archive_survive_consecutive_large_result_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _patch_agent_runtime_paths(tmp_path, monkeypatch)
    results = []

    async def fake_loop(self: Agent, user_message: str) -> None:
        if user_message != "second":
            return
        assert self._runtime_emitter is not None
        assert self._runtime_context is not None
        boundary = DurableToolBoundary(
            self._runtime_emitter,
            self._runtime_context,
            artifact_archive=self._artifact_archive,
            archive_capability=self._archive_capability,
        )
        results.append(await boundary.execute(
            call_id="call-large-second",
            name="read_file",
            arguments={},
            permission="allow",
            executor=lambda: "x" * 17_000,
        ))

    monkeypatch.setattr(Agent, "_chat_anthropic", fake_loop)
    agent = Agent(api_key="fixture-key", is_sub_agent=True)

    asyncio.run(agent.chat("first"))
    store = agent._runtime_store
    archive = agent._artifact_archive
    assert store is not None
    assert archive is not None
    assert agent._artifact_archive_store is store

    asyncio.run(agent.chat("second"))

    assert agent._runtime_store is store
    assert agent._artifact_archive is archive
    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.result == "x" * 17_000
    assert not list((tmp_path / "artifacts").rglob("*"))

    asyncio.run(agent.aclose())
    with pytest.raises(StoreClosedError, match="runtime store is closed"):
        store.read_event_records()


def test_agent_close_is_idempotent_and_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _patch_agent_runtime_paths(tmp_path, monkeypatch)
    _install_noop_chat(monkeypatch)
    agent = Agent(api_key="fixture-key", is_sub_agent=True)

    asyncio.run(agent.chat("first"))
    store = agent._runtime_store
    assert store is not None

    asyncio.run(agent.aclose())
    asyncio.run(agent.aclose())

    with pytest.raises(StoreClosedError):
        store.read_event_records()
    with pytest.raises(AgentClosedError, match="agent runtime is closed"):
        asyncio.run(agent.chat("after close"))


def test_caller_owned_store_survives_agent_close_and_child_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _install_noop_chat(monkeypatch)
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
        parent = Agent(
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
            artifact_archive=archive,
        )
        child = Agent(
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
            artifact_archive=archive,
        )

        asyncio.run(parent.chat("parent"))
        asyncio.run(child.chat("child"))
        asyncio.run(child.aclose())
        asyncio.run(parent.chat("parent again"))
        asyncio.run(parent.aclose())

        store.read_event_records()
        assert store.connection is not None


def test_active_facade_rejects_archive_store_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _patch_agent_runtime_paths(tmp_path, monkeypatch)
    _install_noop_chat(monkeypatch)
    agent = Agent(api_key="fixture-key", is_sub_agent=True)

    asyncio.run(agent.chat("first"))
    bound_store = agent._artifact_archive_store
    assert bound_store is agent._runtime_store
    agent._artifact_archive_store = None

    with pytest.raises(RuntimeResourceMismatchError, match="different runtime store"):
        agent._setup_runtime_facade()

    agent._artifact_archive_store = bound_store
    asyncio.run(agent.aclose())


def test_closed_metadata_store_does_not_return_an_artifact_reference(tmp_path: Path):
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite")
    archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
    store.close()

    with pytest.raises(ArtifactMetadataError, match="runtime store is closed"):
        archive.archive("x" * 17_000, scope="tool-result")


def test_existing_artifact_can_rebuild_metadata_mirror_after_reopen(tmp_path: Path):
    root = tmp_path / "artifacts"
    value = "x" * 17_000
    original = ArtifactArchive(root).archive(value, scope="tool-result")

    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as reopened_store:
        reopened_archive = ArtifactArchive(root, metadata_store=reopened_store)
        same = reopened_archive.archive(value, scope="tool-result")

        assert same.ref == original.ref
        metadata = reopened_store.read_artifact_metadata(same.ref)
        assert metadata is not None
        assert metadata["sha256"] == same.sha256
        assert metadata["size_bytes"] == same.size_bytes
