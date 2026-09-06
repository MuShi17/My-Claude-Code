"""Tests for model thinking-effort normalization and API mappings."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mini_claude.agent import (
    Agent,
    DEFAULT_THINKING_EFFORT,
    ProviderContentNormalizationError,
    _normalize_provider_text,
    _get_anthropic_request_max_tokens,
    _normalize_thinking_effort,
    _thinking_request_params,
)


def test_default_thinking_effort_is_max():
    assert DEFAULT_THINKING_EFFORT == "max"
    assert _normalize_thinking_effort(None) == "max"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("none", "none"), ("off", "none"), ("low", "low"), ("high", "high"), ("max", "max")],
)
def test_normalize_thinking_effort(value: str, expected: str):
    assert _normalize_thinking_effort(value) == expected


def test_invalid_thinking_effort_is_rejected():
    with pytest.raises(ValueError, match="Invalid thinking effort"):
        _normalize_thinking_effort("medium")


def test_anthropic_request_uses_context_envelope_instead_of_fixed_output_cap():
    assert _get_anthropic_request_max_tokens("deepseek-v4-flash") == 200000
    assert _get_anthropic_request_max_tokens("claude-opus-4-6") == 200000


def test_anthropic_thinking_block_serialization_preserves_signature():
    block = SimpleNamespace(type="thinking", thinking="reasoning", signature="sig-123")

    assert Agent._block_to_dict(block) == {
        "type": "thinking",
        "thinking": "reasoning",
        "signature": "sig-123",
    }


def test_provider_text_normalizer_accepts_empty_strings():
    assert _normalize_provider_text(
        "",
        provider="anthropic",
        block_kind="thinking",
        block_index=0,
    ) == ""


@pytest.mark.parametrize(
    ("value", "value_type"),
    [
        (None, "null"),
        ({"secret": "do-not-log"}, "mapping"),
        (["not", "text"], "sequence"),
        (SimpleNamespace(text="wrapped"), "object"),
    ],
)
def test_provider_text_normalizer_rejects_non_strings_without_payloads(value, value_type):
    with pytest.raises(ProviderContentNormalizationError) as error:
        _normalize_provider_text(
            value,
            provider="anthropic",
            block_kind="thinking",
            block_index=3,
        )

    assert error.value.value_type == value_type
    message = str(error.value)
    assert "provider=anthropic" in message
    assert "block_kind=thinking" in message
    assert "block_index=3" in message
    assert f"value_type={value_type}" in message
    assert "do-not-log" not in message


@pytest.mark.parametrize(
    ("field", "value", "block_kind"),
    [
        ("reasoning_content", 0, "thinking"),
        ("reasoning_content", False, "thinking"),
        ("content", [], "text"),
    ],
)
def test_openai_stream_rejects_falsey_non_string_deltas(field, value, block_kind):
    class FakeStream:
        def __aiter__(self):
            delta = SimpleNamespace(
                reasoning_content=value if field == "reasoning_content" else None,
                content=value if field == "content" else None,
                tool_calls=None,
            )
            self._chunks = iter([
                SimpleNamespace(
                    usage=None,
                    choices=[SimpleNamespace(delta=delta, finish_reason=None)],
                )
            ])
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration:
                raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            return FakeStream()

    agent = Agent(
        api_base="https://fake-provider.invalid/v1",
        api_key="fixture-key",
        model="deepseek-v4-flash",
        is_sub_agent=True,
    )
    agent._openai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    with pytest.raises(ProviderContentNormalizationError) as error:
        asyncio.run(agent._call_openai_stream())

    assert error.value.block_kind == block_kind
    assert error.value.value_type in {"bool", "int", "sequence"}


def test_deepseek_anthropic_mapping_uses_output_config_effort():
    assert _thinking_request_params(
        "deepseek-v4-flash", "max", use_openai=False
    ) == {
        "thinking": {"type": "enabled"},
        "output_config": {"effort": "max"},
    }


def test_deepseek_openai_mapping_uses_reasoning_effort():
    assert _thinking_request_params(
        "deepseek-v4-flash", "high", use_openai=True
    ) == {
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }


def test_none_disables_thinking_for_both_backends():
    assert _thinking_request_params("deepseek-v4-flash", "none", use_openai=False) == {
        "thinking": {"type": "disabled"},
    }
    assert _thinking_request_params("deepseek-v4-flash", "none", use_openai=True) == {
        "thinking": {"type": "disabled"},
    }


def test_non_reasoning_openai_model_does_not_receive_reasoning_effort():
    assert _thinking_request_params("gpt-4o", "max", use_openai=True) == {}
