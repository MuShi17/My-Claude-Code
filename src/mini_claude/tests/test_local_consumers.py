"""P0 tests that cross the real local SDK and CLI consumer boundaries."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

try:  # Anthropic >= 1.4 uses the httpx2 compatibility package.
    import httpx2 as anthropic_httpx
except ImportError:  # pragma: no cover - exercised by older supported SDKs.
    anthropic_httpx = httpx

from mini_claude.agent import Agent
from mini_claude.archive_capability import ToolResultArchiveCapability
from mini_claude.archive_projection import format_terminal_tool_result
from mini_claude.artifact_archive import ArtifactArchive
from mini_claude.runtime_event import canonical_json_bytes
from mini_claude.runtime_lifecycle import DurableToolBoundary
from mini_claude.runtime_store import SQLiteRuntimeStore
from mini_claude.tools import _read_file, _truncate_result


LARGE_CONTENT = "内容🙂\n" * 4_500
CLI_LARGE_CONTENT = "内容🙂\n" * 100_000


def _expected_read_file_archive_ref(path: str) -> str:
    """Reconstruct the deterministic archive ref for the CLI fixture.

    The default fixture model has enough budget to receive the bounded
    50,000-character read_file result in full.  The loopback model still needs
    a known ref to exercise the subsequent ArchiveRead consumer path.
    """

    result = _truncate_result(_read_file({"file_path": path}))
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    return f"artifact:sha256:{digest}"


def _sse_event(event_type: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


def _anthropic_stream_body(text: str = "ack") -> bytes:
    return b"".join(
        (
            _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg-fixture",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "fixture-model",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 11, "output_tokens": 0},
                    },
                },
            ),
            _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                },
            ),
            _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            _sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            _sse_event("message_stop", {"type": "message_stop"}),
        )
    )


def _anthropic_stream_body_with_tool(
    name: str, arguments: str, call_id: str = "call-agent-read"
) -> bytes:
    return b"".join(
        (
            _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg-tool-fixture",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "fixture-model",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 11, "output_tokens": 0},
                    },
                },
            ),
            _sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": {},
                    },
                },
            ),
            _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": arguments},
                },
            ),
            _sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            _sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            _sse_event("message_stop", {"type": "message_stop"}),
        )
    )


def _openai_stream_body(text: str = "ack") -> bytes:
    chunks = [
        {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": text},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
        {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
        },
    ]
    return b"".join(
        b"data: "
        + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"


def _provider_tool_content(provider: str, messages: list[dict[str, Any]]) -> Any:
    if provider == "anthropic":
        return next(
            block["content"]
            for message in messages
            if message.get("role") == "user"
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_result"
        )
    return next(message["content"] for message in messages if message.get("role") == "tool")


def _provider_client(
    provider: str,
    captured: list[dict[str, Any]],
    *,
    response_text: str = "ack",
):
    if provider == "anthropic":
        def handler(request: Any):
            captured.append(json.loads(request.content))
            return anthropic_httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_anthropic_stream_body(response_text),
            )

        return AsyncAnthropic(
            api_key="fixture-key",
            base_url="https://fixture.invalid",
            http_client=anthropic_httpx.AsyncClient(
                transport=anthropic_httpx.MockTransport(handler)
            ),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_openai_stream_body(response_text),
        )

    return AsyncOpenAI(
        api_key="fixture-key",
        base_url="https://fixture.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _agent_chat_provider_client(
    provider: str,
    captured: list[dict[str, Any]],
    tool_arguments: str,
):
    if provider == "anthropic":
        def handler(request: Any):
            captured.append(json.loads(request.content))
            if len(captured) == 1:
                body = _anthropic_stream_body_with_tool(
                    "read_file", tool_arguments
                )
            else:
                body = _anthropic_stream_body("done")
            return anthropic_httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body,
            )

        return AsyncAnthropic(
            api_key="fixture-key",
            base_url="https://fixture.invalid",
            http_client=anthropic_httpx.AsyncClient(
                transport=anthropic_httpx.MockTransport(handler)
            ),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            body = _openai_stream_body_with_tool("read_file", tool_arguments)
        else:
            body = _openai_stream_body("done")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    return AsyncOpenAI(
        api_key="fixture-key",
        base_url="https://fixture.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def _build_agent_with_large_result(tmp_path: Path, provider: str):
    store = SQLiteRuntimeStore(tmp_path / f"{provider}.sqlite")
    archive = ArtifactArchive(tmp_path / f"{provider}-artifacts", metadata_store=store)
    agent = Agent(
        api_base="https://fixture.invalid/v1" if provider == "openai" else None,
        api_key="fixture-key",
        model="fixture-model",
        thinking_effort="none",
        custom_system_prompt="fixture system",
        is_sub_agent=True,
        runtime_store=store,
        artifact_archive=archive,
        runtime_session_id="session-consumer",
        runtime_run_id=f"run-consumer-{provider}",
        runtime_context_id=f"context-consumer-{provider}",
    )
    agent._ask_count = 1
    agent._setup_runtime_facade()
    assert agent._runtime_context is not None
    assert agent._runtime_emitter is not None
    boundary = DurableToolBoundary(
        agent._runtime_emitter,
        agent._runtime_context,
        artifact_archive=archive,
        archive_capability=agent._archive_capability,
    )
    result = await boundary.execute(
        call_id="call-large-consumer",
        name="read_file",
        arguments={"file_path": "sample.txt"},
        executor=lambda: LARGE_CONTENT,
    )
    assert result.success is True
    return store, archive, agent, result


async def _build_agent_with_binary_result(tmp_path: Path, provider: str):
    store = SQLiteRuntimeStore(tmp_path / f"{provider}-binary.sqlite")
    archive = ArtifactArchive(
        tmp_path / f"{provider}-binary-artifacts", metadata_store=store
    )
    agent = Agent(
        api_base="https://fixture.invalid/v1" if provider == "openai" else None,
        api_key="fixture-key",
        model="fixture-model",
        thinking_effort="none",
        custom_system_prompt="fixture system",
        is_sub_agent=True,
        runtime_store=store,
        artifact_archive=archive,
        runtime_session_id="session-consumer",
        runtime_run_id=f"run-binary-consumer-{provider}",
        runtime_context_id=f"context-binary-consumer-{provider}",
    )
    agent._ask_count = 1
    agent._setup_runtime_facade()
    assert agent._runtime_context is not None
    assert agent._runtime_emitter is not None
    boundary = DurableToolBoundary(
        agent._runtime_emitter,
        agent._runtime_context,
        artifact_archive=archive,
        archive_capability=agent._archive_capability,
    )
    value = bytes(range(256)) * 80
    result = await boundary.execute(
        call_id="call-binary-consumer",
        name="read_file",
        arguments={"file_path": "sample.bin"},
        executor=lambda: value,
    )
    assert result.success is True
    return store, archive, agent, result


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("capacity_rescue", [False, True])
def test_actual_agent_sdk_receives_full_or_capacity_rescue_projection(
    tmp_path: Path, provider: str, capacity_rescue: bool
):
    async def scenario() -> None:
        store, _archive, agent, _result = await _build_agent_with_large_result(
            tmp_path, provider
        )
        try:
            if capacity_rescue:
                agent.effective_window = 400
            agent._refresh_provider_context_from_canonical()
            captured: list[dict[str, Any]] = []
            client = _provider_client(provider, captured)
            try:
                if provider == "anthropic":
                    agent._anthropic_client = client
                else:
                    agent._openai_client = client
                request_messages = (
                    agent._anthropic_messages
                    if provider == "anthropic"
                    else agent._openai_messages
                )
                agent._start_runtime_model_call(
                    "request-consumer", provider, {"messages": request_messages}
                )
                if provider == "anthropic":
                    response = await agent._call_anthropic_stream()
                    assert response.content[0].text == "ack"
                else:
                    response = await agent._call_openai_stream()
                    assert response["choices"][0]["message"]["content"] == "ack"
            finally:
                await client.close()

            assert len(captured) == 1
            assert (
                len(canonical_json_bytes(captured[0]["messages"]))
                <= agent._provider_budget_bytes()
            )
            wire_content = _provider_tool_content(provider, captured[0]["messages"])
            if capacity_rescue:
                rescue = json.loads(wire_content)
                assert rescue["kind"] == "bounded_ref"
                assert rescue["truncated"] is True
                assert rescue["preview_chars"] <= 4_000
                assert rescue["next_offset"] == rescue["preview_chars"]
                assert "ArchiveRead" in rescue["read_instructions"]
            else:
                assert wire_content == LARGE_CONTENT
                assert len(wire_content) > 16_000

            tool_definitions = captured[0].get("tools", [])
            names = [
                item.get("name") or item.get("function", {}).get("name")
                for item in tool_definitions
            ]
            assert "ArchiveRead" in names
        finally:
            await agent.aclose()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_actual_agent_sdk_binary_rescue_preserves_byte_continuation(
    tmp_path: Path, provider: str
):
    async def scenario() -> None:
        store, _archive, agent, _result = await _build_agent_with_binary_result(
            tmp_path, provider
        )
        try:
            agent.effective_window = 400
            agent._refresh_provider_context_from_canonical()
            captured: list[dict[str, Any]] = []
            client = _provider_client(provider, captured)
            try:
                if provider == "anthropic":
                    agent._anthropic_client = client
                else:
                    agent._openai_client = client
                request_messages = (
                    agent._anthropic_messages
                    if provider == "anthropic"
                    else agent._openai_messages
                )
                agent._start_runtime_model_call(
                    "request-binary-consumer", provider, {"messages": request_messages}
                )
                if provider == "anthropic":
                    await agent._call_anthropic_stream()
                else:
                    await agent._call_openai_stream()
            finally:
                await client.close()

            assert len(captured) == 1
            assert (
                len(canonical_json_bytes(captured[0]["messages"]))
                <= agent._provider_budget_bytes()
            )
            wire_content = _provider_tool_content(provider, captured[0]["messages"])
            rescue = json.loads(wire_content)
            assert rescue["kind"] == "bounded_ref"
            assert rescue["unit"] == "bytes"
            assert rescue["truncated"] is True
            assert rescue["preview"].startswith("base64:")
            assert rescue["preview_bytes"] == rescue["next_offset"]
            assert rescue["preview_bytes"] == len(base64.b64decode(rescue["preview"][7:]))
            assert rescue["next_offset"] > 0
            assert "ArchiveRead" in rescue["read_instructions"]
        finally:
            await agent.aclose()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_actual_agent_sdk_receives_stale_and_archive_page_without_terminal_substitution(
    tmp_path: Path, provider: str
):
    async def scenario() -> None:
        store, _archive, agent, result = await _build_agent_with_large_result(
            tmp_path, provider
        )
        client = None
        try:
            assert agent._runtime_emitter is not None
            assert agent._runtime_context is not None
            agent._emit_canonical_user_event("next step")
            agent._refresh_provider_context_from_canonical()
            stale_content = _provider_tool_content(
                provider,
                agent._anthropic_messages
                if provider == "anthropic"
                else agent._openai_messages,
            )
            stale = json.loads(stale_content)
            assert stale["kind"] == "bounded_ref"
            assert "preview" not in stale
            assert "ArchiveRead" in stale["read_instructions"]

            boundary = DurableToolBoundary(
                agent._runtime_emitter,
                agent._runtime_context,
                artifact_archive=agent._artifact_archive,
                archive_capability=agent._archive_capability,
            )
            page = await boundary.execute(
                call_id="call-archive-page",
                name="ArchiveRead",
                arguments={
                    "operation": "read",
                    "ref": result.result["ref"],
                    "offset": 0,
                    "limit": 32,
                },
                executor=lambda: agent._execute_tool_call(
                    "ArchiveRead",
                    {
                        "operation": "read",
                        "ref": result.result["ref"],
                        "offset": 0,
                        "limit": 32,
                    },
                ),
            )
            assert page.success is True
            agent._refresh_provider_context_from_canonical()
            captured: list[dict[str, Any]] = []
            client = _provider_client(provider, captured)
            if provider == "anthropic":
                agent._anthropic_client = client
            else:
                agent._openai_client = client
            request_messages = (
                agent._anthropic_messages
                if provider == "anthropic"
                else agent._openai_messages
            )
            agent._start_runtime_model_call(
                "request-page-consumer", provider, {"messages": request_messages}
            )
            if provider == "anthropic":
                await agent._call_anthropic_stream()
            else:
                await agent._call_openai_stream()
            assert len(captured) == 1
            assert (
                len(canonical_json_bytes(captured[0]["messages"]))
                <= agent._provider_budget_bytes()
            )
            wire = json.dumps(captured[0]["messages"], ensure_ascii=False)
            assert "内容🙂" in wire
            assert '"tool_use"' in wire if provider == "anthropic" else '"tool_calls"' in wire
            assert "archive_page" in wire
            assert "terminal" not in wire
        finally:
            if client is not None:
                await client.close()
            await agent.aclose()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_actual_agent_sdk_without_archive_capability_fails_closed(
    tmp_path: Path, provider: str
):
    async def scenario() -> None:
        store, _archive, agent, _result = await _build_agent_with_large_result(
            tmp_path, provider
        )
        try:
            agent._archive_capability = None
            agent._refresh_provider_context_from_canonical()
            captured: list[dict[str, Any]] = []
            client = _provider_client(provider, captured)
            try:
                if provider == "anthropic":
                    agent._anthropic_client = client
                else:
                    agent._openai_client = client
                request_messages = (
                    agent._anthropic_messages
                    if provider == "anthropic"
                    else agent._openai_messages
                )
                agent._start_runtime_model_call(
                    "request-no-capability", provider, {"messages": request_messages}
                )
                if provider == "anthropic":
                    await agent._call_anthropic_stream()
                else:
                    await agent._call_openai_stream()
            finally:
                await client.close()

            assert len(captured) == 1
            wire_content = _provider_tool_content(provider, captured[0]["messages"])
            error = json.loads(wire_content)
            assert error["kind"] == "archive_read_error"
            assert error["error_type"] == "capability_unavailable"
            assert "ArchiveRead" not in json.dumps(captured[0].get("tools", []))
            assert str(tmp_path) not in json.dumps(captured[0], ensure_ascii=False)
        finally:
            await agent.aclose()
            store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_public_agent_tool_loop_reaches_second_provider_request(
    tmp_path: Path, provider: str
):
    async def scenario() -> None:
        database = tmp_path / f"{provider}-loop.sqlite"
        store = SQLiteRuntimeStore(database)
        archive = ArtifactArchive(
            tmp_path / f"{provider}-loop-artifacts", metadata_store=store
        )
        source = tmp_path / f"{provider}-loop.txt"
        source.write_text(LARGE_CONTENT, encoding="utf-8")
        agent = Agent(
            api_base="https://fixture.invalid/v1" if provider == "openai" else None,
            api_key="fixture-key",
            model="fixture-model",
            thinking_effort="none",
            custom_system_prompt="fixture system",
            is_sub_agent=True,
            runtime_store=store,
            artifact_archive=archive,
        )
        agent.effective_window = 2_000
        captured: list[dict[str, Any]] = []
        client = _agent_chat_provider_client(
            provider,
            captured,
            json.dumps({"file_path": str(source)}, ensure_ascii=False),
        )
        try:
            if provider == "anthropic":
                agent._anthropic_client = client
            else:
                agent._openai_client = client
            await agent.chat(f"read {source}")
        finally:
            await agent.aclose()
            await client.close()
            store.close()

        assert len(captured) == 2
        second_request = captured[1]
        assert (
            len(canonical_json_bytes(second_request["messages"]))
            <= agent._provider_budget_bytes()
        )
        wire_content = _provider_tool_content(provider, second_request["messages"])
        rescue = json.loads(wire_content)
        assert rescue["kind"] == "bounded_ref"
        assert rescue["truncated"] is True
        assert rescue["next_offset"] == rescue["preview_chars"]
        assert rescue["preview"]
        assert "ArchiveRead" in rescue["read_instructions"]
        assert "terminal" not in json.dumps(second_request, ensure_ascii=False)
        tool_definitions = second_request.get("tools", [])
        names = [
            item.get("name") or item.get("function", {}).get("name")
            for item in tool_definitions
        ]
        assert "ArchiveRead" in names

    asyncio.run(scenario())


def test_terminal_formatter_is_content_first_bounded_and_path_free(tmp_path: Path):
    archive = ArtifactArchive(tmp_path / "artifacts")
    ref = archive.archive(
        "终端正文🙂\n" * 2_000,
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
    )
    capability = ToolResultArchiveCapability(archive, session_id="session-consumer")
    value = ref.placeholder()
    text = format_terminal_tool_result(value, capability, max_chars=500)
    assert text is not None
    assert len(text) <= 500
    assert text.startswith("终端正文🙂")
    assert "ArchiveRead" in text
    assert "\\\"kind\\\"" not in text
    assert str(tmp_path) not in text

    page = {
        "kind": "archive_page",
        "page": "页正文🙂" * 200,
        "offset": 0,
        "next_offset": 32,
        "total_units": 400,
        "unit": "chars",
        "has_more": True,
    }
    page_text = format_terminal_tool_result(page, capability, max_chars=500)
    assert page_text is not None
    assert len(page_text) <= 500
    assert page_text.startswith("页正文🙂")
    assert "next_offset=32" in page_text

    error_text = format_terminal_tool_result(
        {
            "kind": "archive_read_error",
            "error_type": "archive_store_closed",
            "message": "archive store is closed",
        },
        capability,
    )
    assert error_text == "Error [archive_store_closed]: archive store is closed"

    closed_store = SQLiteRuntimeStore(tmp_path / "closed.sqlite")
    closed_archive = ArtifactArchive(
        tmp_path / "closed-artifacts", metadata_store=closed_store
    )
    closed_ref = closed_archive.archive(
        "closed content",
        mime_type="text/plain",
        encoding="utf-8",
        scope="tool-result",
    )
    closed_capability = ToolResultArchiveCapability(
        closed_archive, session_id="session-consumer"
    )
    closed_store.close()
    closed_text = format_terminal_tool_result(
        closed_ref.placeholder(), closed_capability
    )
    assert closed_text == "Error [archive_store_closed]: archive store is closed"


def test_archive_and_canonical_bytes_are_unchanged_by_all_local_projections(
    tmp_path: Path,
):
    async def scenario() -> None:
        store, archive, agent, result = await _build_agent_with_large_result(
            tmp_path, "openai"
        )
        try:
            ref = result.result["ref"]
            artifact_before = archive.read(ref, max_bytes=64 * 1024)
            metadata_before = canonical_json_bytes(archive.metadata(ref))
            sha256_before = archive.inspect(ref).sha256
            events_before = tuple(
                (ordinal, canonical_json_bytes(event.to_dict()))
                for ordinal, event in store.read_event_records()
            )
            ref_before = ref
            agent._refresh_provider_context_from_canonical()
            provider_messages_before = deepcopy(agent._openai_messages)
            assert format_terminal_tool_result(
                result.result, agent._archive_capability
            ) is not None
            assert agent._archive_capability is not None
            page = json.loads(
                agent._archive_capability.execute(
                    {"operation": "read", "ref": ref, "offset": 0, "limit": 16}
                )
            )
            assert page["kind"] == "archive_page"
            agent.effective_window = 400
            agent._refresh_provider_context_from_canonical()

            assert ref == ref_before
            assert archive.read(ref, max_bytes=64 * 1024) == artifact_before
            assert canonical_json_bytes(archive.metadata(ref)) == metadata_before
            assert tuple(
                (ordinal, canonical_json_bytes(event.to_dict()))
                for ordinal, event in store.read_event_records()
            ) == events_before
            assert provider_messages_before != agent._openai_messages
            assert archive.inspect(ref).sha256 == sha256_before
        finally:
            await agent.aclose()
            store.close()

    asyncio.run(scenario())


class _LoopbackProtocolHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol hook.
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body)
        with self.lock:
            self.requests.append(payload)
            request_number = len(self.requests)
        if request_number == 1:
            prompt = str(payload.get("messages", [{}])[-1].get("content", ""))
            file_path = prompt[5:].strip() if prompt.lower().startswith("read ") else prompt
            tool_args = json.dumps(
                {"file_path": file_path},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            response = _openai_stream_body_with_tool("read_file", tool_args)
        else:
            response = _openai_stream_body("done")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


class _LoopbackArchiveFollowupHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    lock = threading.Lock()
    source_path = ""
    mode = "page"

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol hook.
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        with self.lock:
            self.requests.append(payload)
            request_number = len(self.requests)

        if request_number == 1:
            arguments = json.dumps(
                {"file_path": self.source_path},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            response = _openai_stream_body_with_tool(
                "read_file", arguments, call_id="call-cli-read"
            )
        elif request_number == 2:
            ref = ""
            for message in payload.get("messages", []):
                if message.get("role") != "tool":
                    continue
                try:
                    value = json.loads(message.get("content", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict) and isinstance(value.get("ref"), str):
                    ref = value["ref"]
                    break
            if not ref:
                ref = _expected_read_file_archive_ref(self.source_path)
            offset = 0 if self.mode == "page" else 999_999
            arguments = json.dumps(
                {
                    "operation": "read",
                    "ref": ref,
                    "offset": offset,
                    "limit": 32,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            response = _openai_stream_body_with_tool(
                "ArchiveRead", arguments, call_id="call-cli-archive"
            )
        else:
            response = _openai_stream_body("done")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


def _openai_stream_body_with_tool(
    name: str, arguments: str, call_id: str = "call-cli-read"
) -> bytes:
    chunks = [
        {
            "id": "chatcmpl-tool",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-tool",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
        {
            "id": "chatcmpl-tool",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture-model",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
        },
    ]
    return b"".join(
        b"data: "
        + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n\n"
        for chunk in chunks
    ) + b"data: [DONE]\n\n"


def test_real_cli_subprocess_uses_loopback_protocol_and_resume(tmp_path: Path):
    _LoopbackProtocolHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackProtocolHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = tmp_path / "large.txt"
        source.write_text(LARGE_CONTENT, encoding="utf-8")
        cli_home = tmp_path / "cli-home"
        cli_home.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(Path(__file__).parents[2]),
                "HOME": str(cli_home),
                "USERPROFILE": str(cli_home),
                "MINI_CLAUDE_RUNTIME_DIR": str(cli_home / ".mini-claude"),
                "OPENAI_API_KEY": "fixture-key",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "MINI_CLAUDE_THINKING_EFFORT": "none",
                "PYTHON_DOTENV_DISABLED": "1",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        first = subprocess.run(
            [
                sys.executable,
                "-m",
                "mini_claude",
                "--no-thinking",
                "--model",
                "fixture-model",
                "read",
                str(source),
            ],
            cwd=Path(__file__).parents[2],
            env=env,
            capture_output=True,
            text=False,
            timeout=60,
        )
        first_stdout = first.stdout.decode("utf-8", errors="replace")
        first_stderr = first.stderr.decode("utf-8", errors="replace")
        assert first.returncode == 0, first_stderr + first_stdout
        assert "内容🙂" in first_stdout
        assert "ArchiveRead" in first_stdout
        assert len(_LoopbackProtocolHandler.requests) == 2
        assert any(cli_home.joinpath(".mini-claude").rglob("session.v2.json"))

        second = subprocess.run(
            [
                sys.executable,
                "-m",
                "mini_claude",
                "--resume",
                "--no-thinking",
                "--model",
                "fixture-model",
                "continue",
            ],
            cwd=Path(__file__).parents[2],
            env=env,
            capture_output=True,
            text=False,
            timeout=60,
        )
        second_stdout = second.stdout.decode("utf-8", errors="replace")
        second_stderr = second.stderr.decode("utf-8", errors="replace")
        assert second.returncode == 0, second_stderr + second_stdout
        assert "done" in second_stdout
        assert len(_LoopbackProtocolHandler.requests) == 3
        assert "bounded_ref" not in first_stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("mode", ["page", "error"])
def test_real_cli_subprocess_displays_archive_page_and_error(
    tmp_path: Path, mode: str
):
    _LoopbackArchiveFollowupHandler.requests = []
    source = tmp_path / "large.txt"
    source.write_text(CLI_LARGE_CONTENT, encoding="utf-8")
    _LoopbackArchiveFollowupHandler.source_path = str(source)
    _LoopbackArchiveFollowupHandler.mode = mode
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _LoopbackArchiveFollowupHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cli_home = tmp_path / "cli-home"
        cli_home.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(Path(__file__).parents[2]),
                "HOME": str(cli_home),
                "USERPROFILE": str(cli_home),
                "MINI_CLAUDE_RUNTIME_DIR": str(cli_home / ".mini-claude"),
                "OPENAI_API_KEY": "fixture-key",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "MINI_CLAUDE_THINKING_EFFORT": "none",
                "PYTHON_DOTENV_DISABLED": "1",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mini_claude",
                "--no-thinking",
                "--model",
                "fixture-model",
                "read",
                str(source),
            ],
            cwd=Path(__file__).parents[2],
            env=env,
            capture_output=True,
            text=False,
            timeout=60,
        )
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        assert completed.returncode == 0, stderr + stdout
        assert len(_LoopbackArchiveFollowupHandler.requests) == 3
        if mode == "page":
            assert "内容🙂" in stdout
            assert "next_offset=32" in stdout
            assert "has_more=true" in stdout
        else:
            assert "Error [invalid_range]" in stdout
        assert "Traceback" not in stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
