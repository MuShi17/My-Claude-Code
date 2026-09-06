"""Deterministic Provider-visible tool content tests."""

from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from pathlib import Path

import httpx
from openai import AsyncOpenAI

import pytest

from mini_claude.provider_content import (
    ProviderToolContentError,
    materialize_tool_result,
    materialized_content_bytes,
)
from mini_claude.projections import CanonicalModelContextAdapter
from mini_claude.projections.base import EventRecord
from mini_claude.projections.incremental_replay import IncrementalModelReplayCursor
from mini_claude.projections.model_replay_projection import ModelReplayResult
from mini_claude.runtime_event import RuntimeEvent
from mini_claude.runtime_store import SQLiteRuntimeStore

from runtime_fixtures import DeterministicIdFactory, build_scenario, scenario_events


def _events(provider: str = "anthropic") -> list[RuntimeEvent]:
    return [
        RuntimeEvent.from_dict(item)
        for item in scenario_events(
            build_scenario(), ids=DeterministicIdFactory("provider-content"), provider=provider
        )
    ]


def test_structured_tool_results_use_compact_sorted_utf8_json():
    first = {"z": ["值", True, None], "a": {"nested": 1}}
    second = {"a": {"nested": 1}, "z": ["值", True, None]}

    expected = '{"a":{"nested":1},"z":["值",true,null]}'
    assert materialize_tool_result(first, provider="anthropic") == expected
    assert materialize_tool_result(second, provider="anthropic") == expected
    assert materialize_tool_result(first, provider="openai") == expected


def test_only_explicitly_valid_blocks_remain_anthropic_blocks():
    valid = [{"type": "text", "text": "多模态文本"}]
    arbitrary = [{"kind": "row", "value": 1}]

    result = materialize_tool_result(valid, provider="anthropic")
    assert result == [{"text": "多模态文本", "type": "text"}]
    assert materialize_tool_result(valid, provider="openai") == '[{"text":"多模态文本","type":"text"}]'
    assert materialize_tool_result(arbitrary, provider="anthropic") == '[{"kind":"row","value":1}]'


def test_non_json_tool_result_is_rejected_without_payload_in_error():
    secret = object()
    with pytest.raises(ProviderToolContentError) as error:
        materialize_tool_result({"secret": secret}, provider="anthropic")

    assert error.value.value_type == "mapping"
    assert "secret" not in str(error.value)
    assert "object" not in str(error.value)


def test_non_finite_numbers_are_rejected():
    with pytest.raises(ProviderToolContentError):
        materialize_tool_result({"value": float("nan")}, provider="anthropic")


def test_bounded_placeholder_has_identical_first_and_replayed_bytes(tmp_path: Path):
    events = _events()
    bounded_ref = {
        "kind": "bounded_ref",
        "ref": "artifact:sha256:abc123",
        "sha256": "sha256:abc123",
        "size_bytes": 24576,
        "inline": "第一行\n第二行",
        "truncated": True,
    }
    response = deepcopy(events[-1].to_dict())
    response["content"]["result"] = bounded_ref
    response_event = RuntimeEvent.from_dict(response)
    first = materialize_tool_result(bounded_ref, provider="anthropic")
    assert isinstance(first, str)

    database = tmp_path / "runtime.sqlite"
    with SQLiteRuntimeStore(database) as store:
        for event in [*events[:-1], response_event]:
            store.append(event)
        context = CanonicalModelContextAdapter().build(store, provider="anthropic")
        tool_result = next(
            block
            for message in context.messages
            if message.get("role") == "user"
            for block in message.get("content", [])
            if block.get("type") == "tool_result"
        )
        assert tool_result["content"] == first
        first_bytes = materialized_content_bytes(first)

    with SQLiteRuntimeStore(database) as reopened:
        context = CanonicalModelContextAdapter().build(reopened, provider="anthropic")
        tool_result = next(
            block
            for message in context.messages
            if message.get("role") == "user"
            for block in message.get("content", [])
            if block.get("type") == "tool_result"
        )
        assert materialized_content_bytes(tool_result["content"]) == first_bytes
        assert json.loads(tool_result["content"]) == bounded_ref


def _neutral_tool_result() -> ModelReplayResult:
    messages = (
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-read",
                    "name": "read_file",
                    "arguments": {"z": "值", "file_path": "sample.txt"},
                },
                {
                    "id": "call-list",
                    "name": "list_files",
                    "arguments": {"path": "."},
                },
            ],
            "runtime_event_id": "event-assistant",
        },
        {
            "role": "tool",
            "tool_call_id": "call-read",
            "content": {"z": [2, 1], "a": True},
            "runtime_event_id": "event-tool-read",
        },
        {
            "role": "tool",
            "tool_call_id": "call-list",
            "content": {"files": ["a.py"]},
            "runtime_event_id": "event-tool-list",
        },
    )
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


def test_openai_adapter_emits_strict_function_wire_shape_and_hides_runtime_ids():
    context = CanonicalModelContextAdapter().build_result(
        _neutral_tool_result(), provider="openai", system_prompt="system"
    )

    assistant = context.messages[1]
    assert assistant == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-read",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"file_path":"sample.txt","z":"值"}',
                },
            },
            {
                "id": "call-list",
                "type": "function",
                "function": {"name": "list_files", "arguments": '{"path":"."}'},
            },
        ],
    }
    assert all("runtime_event_id" not in message for message in context.messages)
    assert context.messages[2]["content"] == '{"a":true,"z":[2,1]}'


def test_openai_sdk_transport_receives_provider_wire_shape():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    context = CanonicalModelContextAdapter().build_result(
        _neutral_tool_result(), provider="openai", system_prompt="system"
    )

    async def invoke() -> None:
        client = AsyncOpenAI(
            api_key="fixture-key",
            base_url="https://fixture.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            await client.chat.completions.create(
                model="fixture-model", messages=list(context.messages)
            )
        finally:
            await client.close()

    asyncio.run(invoke())
    tool_call = captured["messages"][1]["tool_calls"][0]
    assert tool_call["type"] == "function"
    assert isinstance(tool_call["function"]["arguments"], str)
    assert "runtime_event_id" not in json.dumps(captured, ensure_ascii=False)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_cold_warm_and_reopened_store_have_identical_provider_wire_messages(
    tmp_path: Path, provider: str
):
    events = _events(provider)
    database = tmp_path / f"{provider}.sqlite"
    with SQLiteRuntimeStore(database) as store:
        for event in events:
            store.append(event)
        cold = CanonicalModelContextAdapter().build(
            store,
            provider=provider,
            system_prompt="system fixture" if provider == "openai" else None,
        )
        cursor = IncrementalModelReplayCursor()
        cursor.append(EventRecord(ordinal, event) for ordinal, event in store.read_event_records())
        warm = CanonicalModelContextAdapter().build_result(
            cursor.result(),
            provider=provider,
            system_prompt="system fixture" if provider == "openai" else None,
        )
    with SQLiteRuntimeStore(database) as reopened:
        reopened_context = CanonicalModelContextAdapter().build(
            reopened,
            provider=provider,
            system_prompt="system fixture" if provider == "openai" else None,
        )

    assert cold.messages == warm.messages == reopened_context.messages
