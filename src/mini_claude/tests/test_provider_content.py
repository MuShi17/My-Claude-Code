"""Deterministic Provider-visible tool content tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from mini_claude.provider_content import (
    ProviderToolContentError,
    materialize_tool_result,
    materialized_content_bytes,
)
from mini_claude.projections import CanonicalModelContextAdapter
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
