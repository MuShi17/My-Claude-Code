"""Consumer-facing archive projection tests for both Provider formats."""

from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from mini_claude.archive_capability import ToolResultArchiveCapability
from mini_claude.archive_projection import (
    project_archived_tool_results,
    project_terminal_tool_result,
)
from mini_claude.agent import Agent
from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.projections.provider_context import CanonicalModelContextAdapter
from mini_claude.projections.model_replay_projection import ModelReplayResult
from mini_claude.runtime_lifecycle import DurableToolBoundary
from mini_claude.runtime_store import SQLiteRuntimeStore


def _result(messages: tuple[dict, ...]) -> ModelReplayResult:
    return ModelReplayResult(
        projection_version="projection-v1",
        schema_version=1,
        high_water=3,
        source_digest="source",
        digest="digest",
        messages=messages,
        partial_count=0,
        diagnostics=(),
    )


def _fixture(tmp_path: Path, content: str = "首行\n" + "正文🙂\n" * 1200):
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        content,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    placeholder = ref.placeholder()
    messages = (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-read", "name": "read_file", "arguments": {"file_path": "x"}}
            ],
            "runtime_event_id": "event-assistant",
        },
        {
            "role": "tool",
            "tool_call_id": "call-read",
            "content": placeholder,
            "runtime_event_id": "event-tool",
        },
    )
    return archive, ref, capability, messages, content


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_first_use_hydrates_complete_safe_body_for_each_provider(tmp_path: Path, provider: str):
    _archive, ref, capability, messages, content = _fixture(tmp_path)
    context = CanonicalModelContextAdapter().build_result(
        _result(messages),
        provider=provider,
        system_prompt="system" if provider == "openai" else None,
        archive_capability=capability,
        budget_bytes=200_000,
    )

    if provider == "anthropic":
        tool_result = next(
            block
            for message in context.messages
            if message.get("role") == "user"
            for block in message.get("content", [])
            if block.get("type") == "tool_result"
        )
        assert tool_result["content"] == content
    else:
        tool_result = next(message for message in context.messages if message.get("role") == "tool")
        assert tool_result["content"] == content
    assert ref.ref not in str(tool_result.get("content"))


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_capacity_rescue_and_stale_projection_are_actionable(tmp_path: Path, provider: str):
    _archive, ref, capability, messages, content = _fixture(tmp_path)
    rescued = project_archived_tool_results(
        messages,
        capability,
        budget_bytes=2_000,
    )
    rescue = rescued[1]["content"]
    assert rescue["kind"] == "bounded_ref"
    assert rescue["ref"] == ref.ref
    assert rescue["truncated"] is True
    assert rescue["preview"] == content[: rescue["preview_chars"]]
    assert rescue["preview_chars"] <= 4_000
    assert rescue["next_offset"] == rescue["preview_chars"]
    assert "ArchiveRead" in rescue["read_instructions"]

    stale_messages = (*messages, {"role": "user", "content": "next"})
    stale = project_archived_tool_results(stale_messages, capability, budget_bytes=200_000)
    stale_content = stale[1]["content"]
    assert stale_content["kind"] == "bounded_ref"
    assert "preview" not in stale_content
    assert "ArchiveRead" in stale_content["read_instructions"]

    context = CanonicalModelContextAdapter().build_result(
        _result(stale_messages),
        provider=provider,
        system_prompt="system" if provider == "openai" else None,
        archive_capability=capability,
        budget_bytes=200_000,
    )
    wire = json.dumps(context.messages, ensure_ascii=False)
    assert ref.ref in wire
    assert "ArchiveRead" in wire

    blocked = project_archived_tool_results(messages, capability, budget_bytes=1)
    blocked_content = blocked[1]["content"]
    assert blocked_content["kind"] == "archive_read_error"
    assert blocked_content["error_type"] == "capacity_exhausted"
    assert ref.ref in blocked_content["ref"]


def test_complete_canonical_result_is_archived_only_when_projection_needs_it(
    tmp_path: Path,
):
    archive = ArtifactArchive(tmp_path / "artifacts")
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    content = "完整 canonical 正文🙂\n" * 2_000
    messages = (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-full", "name": "read_file", "arguments": {"file_path": "x"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-full", "content": content},
    )

    fit = project_archived_tool_results(messages, capability, budget_bytes=200_000)
    assert fit[1]["content"] == content
    assert not list((tmp_path / "artifacts").rglob("*.bin"))
    assert messages[1]["content"] == content

    rescue = project_archived_tool_results(messages, capability, budget_bytes=2_000)
    envelope = rescue[1]["content"]
    assert envelope["kind"] == "bounded_ref"
    assert envelope["preview"] == content[: envelope["preview_chars"]]
    assert envelope["next_offset"] == envelope["preview_chars"]
    assert archive.inspect(envelope["ref"]).size_bytes > 16_384
    assert messages[1]["content"] == content


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_only_latest_completed_provider_step_is_first_use(
    tmp_path: Path, provider: str
):
    archive = ArtifactArchive(tmp_path / "artifacts")
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    first = "first step 🙂\n" * 800
    second = "latest step 🚀\n" * 800
    messages = (
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-first", "name": "read_file", "arguments": {}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-first", "content": first},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-latest", "name": "read_file", "arguments": {}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-latest", "content": second},
    )

    projected = project_archived_tool_results(
        messages,
        capability,
        budget_bytes=200_000,
    )
    assert projected[2]["content"]["kind"] == "bounded_ref"
    assert projected[4]["content"] == second
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == 1

    context = CanonicalModelContextAdapter().build_result(
        _result(tuple(projected)),
        provider=provider,
        system_prompt="system" if provider == "openai" else None,
        archive_capability=capability,
        budget_bytes=200_000,
    )
    if provider == "anthropic":
        wire = next(
            block["content"]
            for message in context.messages
            if message.get("role") == "user"
            for block in message.get("content", [])
            if isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("tool_use_id") == "call-latest"
        )
    else:
        wire = next(
            message["content"]
            for message in context.messages
            if message.get("role") == "tool"
            and message.get("tool_call_id") == "call-latest"
        )
    assert wire == second


def _archive_read_messages(content: object) -> tuple[dict, ...]:
    return (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-archive-read",
                    "name": "ArchiveRead",
                    "arguments": {
                        "operation": "read",
                        "ref": "artifact:sha256:" + "a" * 64,
                        "offset": 0,
                        "limit": 64,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-archive-read",
            "content": content,
        },
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_archive_read_page_is_non_recursive_for_both_providers(
    tmp_path: Path, provider: str
):
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "archive source 🙂\n" * 200,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    page = capability.execute(
        {
            "operation": "read",
            "ref": ref.ref,
            "offset": 0,
            "limit": 64,
        }
    )
    before = len(list((tmp_path / "artifacts").rglob("*.bin")))
    messages = _archive_read_messages(page)

    projected = project_archived_tool_results(
        messages,
        capability,
        budget_bytes=200_000,
    )
    assert projected[1]["content"] == page
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == before

    context = CanonicalModelContextAdapter().build_result(
        _result(projected),
        provider=provider,
        system_prompt="system" if provider == "openai" else None,
        archive_capability=capability,
        budget_bytes=200_000,
    )
    if provider == "anthropic":
        wire = next(
            block["content"]
            for message in context.messages
            if message.get("role") == "user"
            for block in message.get("content", [])
            if block.get("type") == "tool_result"
        )
    else:
        wire = next(
            message["content"]
            for message in context.messages
            if message.get("role") == "tool"
        )
    wire_value = json.loads(wire) if isinstance(wire, str) else wire
    assert wire_value["kind"] == "archive_page"
    assert wire_value["ref"] == ref.ref
    assert str(wire_value["ref"]).count("artifact:sha256:") == 1

    repeated = project_archived_tool_results(
        messages,
        capability,
        budget_bytes=200_000,
    )
    assert repeated[1]["content"] == page
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == before


def test_archive_read_capacity_error_does_not_archive_page(tmp_path: Path):
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "archive source\n" * 200,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    page = capability.execute(
        {
            "operation": "read",
            "ref": ref.ref,
            "offset": 0,
            "limit": 64,
        }
    )
    messages = (
        {"role": "user", "content": "context " * 2_000},
        *_archive_read_messages(page),
    )
    before = len(list((tmp_path / "artifacts").rglob("*.bin")))

    projected = project_archived_tool_results(
        messages,
        capability,
        budget_bytes=1_000,
    )
    error = projected[-1]["content"]
    assert error["kind"] == "archive_read_error"
    assert error["error_type"] == "capacity_exhausted"
    assert error["ref"] == ref.ref
    assert "smaller limit" in error["message"]
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == before


def test_historical_archive_read_bounded_ref_is_not_rearchived(tmp_path: Path):
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "historical page\n" * 100,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
        metadata={"session_id": "session-a", "run_id": "run-a"},
    )
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    placeholder = ref.placeholder()
    messages = _archive_read_messages(placeholder)
    before = len(list((tmp_path / "artifacts").rglob("*.bin")))

    projected = project_archived_tool_results(messages, capability, budget_bytes=200_000)
    repeated = project_archived_tool_results(
        messages,
        capability,
        budget_bytes=200_000,
    )
    assert projected[1]["content"] == placeholder
    assert repeated[1]["content"] == placeholder
    assert len(list((tmp_path / "artifacts").rglob("*.bin"))) == before


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_archive_aware_live_pipeline_skips_legacy_result_rewriters(
    provider: str, monkeypatch: pytest.MonkeyPatch
):
    agent = Agent(
        api_base="https://fake-provider.invalid/v1" if provider == "openai" else None,
        api_key="fixture-key",
        is_sub_agent=True,
    )
    called: list[str] = []
    methods = (
        "_capture_compression_tool_results",
        "_budget_tool_results_openai",
        "_budget_tool_results_anthropic",
        "_snip_stale_results_openai",
        "_snip_stale_results_anthropic",
        "_microcompact_openai",
        "_microcompact_anthropic",
        "_persist_compression_replacements",
    )
    for method in methods:
        monkeypatch.setattr(
            agent,
            method,
            lambda method=method: called.append(method),
        )

    large_result = "new canonical result 🙂\n" * 1_000
    if provider == "openai":
        agent._openai_messages = [
            {
                "role": "tool",
                "tool_call_id": "call-new",
                "content": large_result,
            }
        ]
    else:
        agent._anthropic_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-new",
                        "content": large_result,
                    }
                ],
            }
        ]
    before = deepcopy(
        agent._openai_messages if provider == "openai" else agent._anthropic_messages
    )
    agent.last_input_token_count = agent.effective_window

    # The repeated call models a retry/next live attempt at high utilization;
    # neither attempt may mutate the newly completed result.
    agent._run_compression_pipeline(archive_aware=True)
    agent._run_compression_pipeline(archive_aware=True)
    assert called == []
    assert (
        agent._openai_messages if provider == "openai" else agent._anthropic_messages
    ) == before


def test_projection_archive_failure_returns_error_without_success_ref(tmp_path: Path):
    class Failure:
        def check(self, point: str) -> None:
            if point in {"artifact.write", "archive.write"}:
                raise RuntimeError("fixture archive write failure")

    archive = ArtifactArchive(tmp_path / "artifacts", fault_hook=Failure())
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    content = "正文🙂\n" * 2_000
    messages = (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-fault", "name": "read_file", "arguments": {}}],
        },
        {"role": "tool", "tool_call_id": "call-fault", "content": content},
    )

    projected = project_archived_tool_results(messages, capability, budget_bytes=2_000)
    error = projected[1]["content"]
    assert error["kind"] == "archive_read_error"
    assert error["error_type"] == "capability_unavailable"
    assert "ref" not in error
    assert not list((tmp_path / "artifacts").rglob("*.json"))


def test_logical_archive_read_page_is_passed_through_without_recursive_archive(
    tmp_path: Path,
) -> None:
    archive = ArtifactArchive(tmp_path / "artifacts")
    capability = ToolResultArchiveCapability(
        archive,
        session_id="session-a",
        run_id="run-a",
    )
    ref = capability.archive_result("archive page body\n" * 20, call_id="call-source", tool_name="fixture")
    page = json.loads(capability.execute({
        "operation": "read",
        "ref": ref.ref,
        "offset": 0,
        "limit": 32,
    }))
    before = sorted(path.name for path in (tmp_path / "artifacts" / "refs").glob("*.json"))
    messages = (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-archive", "name": "ArchiveRead", "arguments": {}}],
        },
        {"role": "tool", "tool_call_id": "call-archive", "content": page},
    )

    projected = project_archived_tool_results(messages, capability, budget_bytes=200_000)

    assert projected[1]["content"] == page
    assert sorted(path.name for path in (tmp_path / "artifacts" / "refs").glob("*.json")) == before
    assert json.dumps(projected[1]["content"]).count("artifact:tool-result:") == 1


def test_stale_ref_can_be_read_back_as_a_normal_bounded_page(tmp_path: Path):
    _archive, ref, capability, messages, content = _fixture(tmp_path)
    stale = project_archived_tool_results(
        (*messages, {"role": "user", "content": "next"}),
        capability,
    )
    placeholder = stale[1]["content"]
    page = json.loads(
        capability.execute(
            {
                "operation": "read",
                "ref": placeholder["ref"],
                "offset": placeholder.get("next_offset", 0),
                "limit": 64,
            }
        )
    )
    assert page["kind"] == "archive_page"
    assert page["page"] == content[:64]
    assert page["ref"] == ref.ref


def test_inaccessible_ref_degrades_to_bounded_error_without_success_placeholder(
    tmp_path: Path,
):
    _archive, ref, capability, messages, _content = _fixture(tmp_path)
    missing = dict(messages[1]["content"])
    missing["ref"] = "artifact:sha256:" + "f" * 64
    missing["sha256"] = "sha256:" + "f" * 64
    projected = project_archived_tool_results(
        ({**messages[0]}, {**messages[1], "content": missing}),
        capability,
        budget_bytes=200_000,
    )
    error = projected[1]["content"]
    assert error["kind"] == "archive_read_error"
    assert error["error_type"] == "not_found"
    assert error["ref"] != ref.ref


def test_terminal_projection_is_bounded_and_does_not_hydrate_full_body(tmp_path: Path):
    _archive, ref, capability, messages, content = _fixture(tmp_path)
    terminal = project_terminal_tool_result(messages[1]["content"], capability)
    assert terminal["kind"] == "archived_tool_result"
    assert terminal["ref"] == ref.ref
    assert terminal["preview"] == content[:4_000]
    assert terminal["truncated"] is True
    assert len(terminal["preview"]) < len(content)
    assert "ArchiveRead" in terminal["read_instructions"]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_real_agent_replay_reaches_fake_provider_with_archive_projection(
    tmp_path: Path, provider: str
):
    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
        agent = Agent(
            api_base="https://fake-provider.invalid/v1" if provider == "openai" else None,
            api_key="fixture-key",
            custom_system_prompt="fixture system",
            is_sub_agent=True,
            runtime_store=store,
            artifact_archive=archive,
            runtime_session_id="session-a",
            runtime_run_id="run-a",
            runtime_context_id="context-a",
        )
        agent._ask_count = 1
        agent._setup_runtime_facade()
        assert agent._archive_capability is not None
        assert [
            item["name"]
            for item in agent._effective_tool_definitions()
            if item["name"] == "ArchiveRead"
        ] == ["ArchiveRead"]

        context = agent._runtime_context
        emitter = agent._runtime_emitter
        assert context is not None and emitter is not None
        boundary = DurableToolBoundary(
            emitter,
            context,
            artifact_archive=archive,
            archive_capability=agent._archive_capability,
        )
        result = asyncio.run(
            boundary.execute(
                call_id="call-large",
                name="read_file",
                arguments={"file_path": "sample.txt"},
                permission="allow",
                executor=lambda: "内容🙂\n" * 4_500,
            )
        )
        assert result.success is True

        # This is the final message shape a fake Provider consumer receives;
        # it is built by replaying the real canonical store, not by injecting
        # a projector result directly into the assertion.
        first_context = agent._refresh_provider_context_from_canonical()
        if provider == "anthropic":
            first_wire = next(
                block
                for message in first_context.messages
                if message.get("role") == "user"
                for block in message.get("content", [])
                if block.get("type") == "tool_result"
            )["content"]
        else:
            first_wire = next(
                message for message in first_context.messages if message.get("role") == "tool"
            )["content"]
        assert first_wire.startswith("内容🙂")
        assert len(first_wire) > 16_000
        assert result.result == "内容🙂\n" * 4_500
        assert not list((tmp_path / "artifacts").rglob("*.bin"))

        replay = agent.project_canonical_model_context()
        assert replay is not None
        assert replay.messages[-1]["content"] == result.result

        agent.effective_window = 400
        rescue_context = agent._refresh_provider_context_from_canonical()
        rescue_wire = (
            next(
                block
                for message in rescue_context.messages
                if message.get("role") == "user"
                for block in message.get("content", [])
                if block.get("type") == "tool_result"
            )["content"]
            if provider == "anthropic"
            else next(message for message in rescue_context.messages if message.get("role") == "tool")["content"]
        )
        rescue_value = json.loads(rescue_wire)
        assert rescue_value["kind"] == "bounded_ref"
        assert rescue_value["preview_chars"] <= 4_000
        ref = rescue_value["ref"]
        assert archive.inspect(ref).size_bytes > 16_000
        read_back = json.loads(
            asyncio.run(
                agent._execute_tool_call(
                    "ArchiveRead",
                    {
                        "operation": "read",
                        "ref": ref,
                        "offset": 0,
                        "limit": 32,
                    },
                )
            )
        )
        assert read_back["kind"] == "archive_page"
        assert read_back["page"].startswith("内容🙂")


def test_archive_read_errors_are_bounded_and_path_free(tmp_path: Path):
    capability = ToolResultArchiveCapability(
        ArtifactArchive(tmp_path / "artifacts"),
        session_id="session-a",
        run_id="run-a",
    )
    missing = json.loads(
        capability.execute(
            {
                "operation": "read",
                "ref": "artifact:sha256:" + "a" * 64,
                "limit": 10,
            }
        )
    )
    assert missing["error_type"] == "not_found"
    assert str(tmp_path) not in json.dumps(missing)
    invalid_range = json.loads(
        capability.execute(
            {
                "operation": "read",
                "ref": "C:\\secret.txt",
                "offset": -1,
            }
        )
    )
    assert invalid_range["error_type"] == "metadata_invalid"


def test_child_agent_receives_derived_capability_without_allowlist_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: list[Agent] = []

    async def fake_run_once(self: Agent, prompt: str) -> dict:
        del prompt
        captured.append(self)
        return {"text": "child result", "tokens": {"input": 0, "output": 0}}

    monkeypatch.setattr(Agent, "run_once", fake_run_once)
    with SQLiteRuntimeStore(tmp_path / "runtime.sqlite") as store:
        archive = ArtifactArchive(tmp_path / "artifacts", metadata_store=store)
        parent = Agent(
            api_key="fixture-key",
            is_sub_agent=True,
            runtime_store=store,
            artifact_archive=archive,
            runtime_session_id="session-a",
            runtime_run_id="run-parent",
            runtime_context_id="context-parent",
        )
        parent._ask_count = 1
        parent._setup_runtime_facade()
        result = asyncio.run(
            parent._execute_agent_tool(
                {"type": "explore", "description": "inspect", "prompt": "read"}
            )
        )
        assert result == "child result"
        child = captured[0]
        assert child._archive_capability is not None
        assert child._archive_capability is not parent._archive_capability
        assert not any(item["name"] == "ArchiveRead" for item in child.tools)
        assert sum(
            item["name"] == "ArchiveRead"
            for item in child._effective_tool_definitions()
        ) == 1


def test_agent_close_waits_for_active_provider_task_before_closing_owned_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "mini_claude.agent.runtime_store_path",
        lambda session_id: tmp_path / f"{session_id}.sqlite",
    )
    monkeypatch.setattr("mini_claude.agent.runtime_data_dir", lambda: tmp_path)
    ready = asyncio.Event()
    release = asyncio.Event()

    async def blocked_loop(self: Agent, user_message: str) -> None:
        del user_message
        ready.set()
        await release.wait()

    monkeypatch.setattr(Agent, "_chat_anthropic", blocked_loop)

    async def scenario() -> None:
        agent = Agent(api_key="fixture-key", is_sub_agent=True)
        chat_task = asyncio.create_task(agent.chat("wait"))
        await ready.wait()
        close_task = asyncio.create_task(agent.aclose())
        await asyncio.sleep(0)
        assert not close_task.done()
        store = agent._runtime_store
        assert store is not None
        assert store.connection is not None
        release.set()
        await chat_task
        await close_task
        with pytest.raises(Exception):
            store.read_event_records()

    asyncio.run(scenario())
