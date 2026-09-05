"""Tests for model thinking-effort normalization and API mappings."""

from __future__ import annotations

import pytest

from mini_claude.agent import (
    DEFAULT_THINKING_EFFORT,
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
