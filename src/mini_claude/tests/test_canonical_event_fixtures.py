"""Contract-level tests for the offline canonical-event fixture kit."""

from __future__ import annotations

import json
from pathlib import Path

from runtime_fixtures import (
    DEFAULT_TIME,
    DeterministicIdFactory,
    FakeProviderScript,
    FixedClock,
    FaultInjector,
    assert_no_secrets,
    build_scenario,
    isolated_home,
    scenario_events,
    stable_diff,
    stable_digest,
)


def test_fixture_digest_is_stable_across_time_ids_and_temp_paths(tmp_path: Path):
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left = scenario_events(
        build_scenario(workspace=left_root),
        clock=FixedClock(DEFAULT_TIME),
        ids=DeterministicIdFactory("left"),
    )
    right = scenario_events(
        build_scenario(workspace=right_root),
        clock=FixedClock(DEFAULT_TIME.replace(microsecond=123000)),
        ids=DeterministicIdFactory("random-uuid-shaped"),
    )

    assert stable_diff(left, right, temp_roots=(str(left_root), str(right_root))) is None
    assert stable_digest(left, temp_roots=(str(left_root), str(right_root))) == stable_digest(
        right, temp_roots=(str(left_root), str(right_root))
    )


def test_fake_anthropic_and_openai_scripts_have_equivalent_semantics():
    scenario = build_scenario()
    anthropic = FakeProviderScript("anthropic", scenario).final_response()
    openai = FakeProviderScript("openai", scenario).final_response()

    assert stable_diff(anthropic, openai) is None
    assert [chunk.kind for chunk in FakeProviderScript("anthropic", scenario).stream()] == [
        "invocation_opened",
        "text",
        "function_call",
        "permission",
        "tool_dispatch",
        "tool_outcome",
        "function_response",
    ]


def test_provider_fixture_is_offline_and_does_not_require_api_key():
    response = FakeProviderScript("anthropic", build_scenario()).final_response()
    assert response["usage"] == {"input_tokens": 10, "output_tokens": 8}
    assert_no_secrets(response)


def test_fault_injector_is_explicit_and_reproducible():
    fault = FaultInjector("dispatch.before", "store.commit")
    try:
        fault.check("dispatch.before")
    except RuntimeError as error:
        assert str(error) == "fixture fault at dispatch.before"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("fault hook did not fire")
    assert fault.triggered == ["dispatch.before"]


def test_isolated_home_never_uses_user_session_directory():
    with isolated_home() as home:
        assert Path.home() == home
        assert not (home / ".mini-claude" / "sessions").exists()


def test_golden_fixture_is_versioned_bounded_and_redacted():
    golden_path = Path(__file__).parent / "golden" / "canonical_runtime_event.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert golden["schema_version"] == 2
    assert set(golden) >= {"events", "messages", "trace", "compaction"}
    assert_no_secrets(golden)
    assert len(json.dumps(golden, ensure_ascii=False)) < 30_000


def test_contract_inputs_are_not_silently_empty():
    events = scenario_events(build_scenario())
    assert events
    kinds: set[str] = set()
    for event in events:
        content = event.get("content") or {}
        actions = event.get("actions") or {}
        if content.get("kind"):
            kinds.add(content["kind"])
        kinds.update(actions)
    assert kinds >= {
        "invocation_opened",
        "function_call",
        "permission",
        "tool_dispatch",
        "tool_outcome",
        "function_response",
    }
