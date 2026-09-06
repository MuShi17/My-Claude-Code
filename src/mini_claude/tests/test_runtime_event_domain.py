"""Canonical RuntimeEvent domain and sink contract tests."""

from __future__ import annotations

import pytest

from mini_claude.event_ids import IdentityFactory, RunContext
from mini_claude.event_sink import (
    CanonicalSink,
    CanonicalSinkError,
    RecordingEventSink,
    RuntimeEventEmitter,
)
from mini_claude.redaction import RedactionPolicy, bound_payload, redact_event_dict, redact_payload
from mini_claude.runtime_event import RuntimeEvent, RuntimeEventValidationError

from runtime_fixtures import FaultInjector, build_scenario, scenario_events


def _event(kind: str = "text") -> RuntimeEvent:
    data = next(
        item
        for item in scenario_events(build_scenario())
        if item.get("content", {}).get("kind") == kind
        or kind in item.get("actions", {})
    )
    return RuntimeEvent.from_dict(data)


def test_fixture_event_is_strict_frozen_canonical_envelope():
    event = _event("invocation_opened")
    assert event.schema_version == 2
    assert event.kind == "invocation_opened"
    assert event.ts == 1767323045678
    assert event.validate() is None
    with pytest.raises(TypeError):
        event.content["new"] = "not allowed"  # type: ignore[index]
    assert event.canonical_bytes() == event.canonical_bytes()


def test_canonical_digest_is_independent_of_input_mapping_order():
    base = _event("text").to_dict()
    reordered = {
        "metadata": base["metadata"],
        "content": base["content"],
        "author": base["author"],
        "role": base["role"],
        "partial": base["partial"],
        "ts": base["ts"],
        "turn_id": base["turn_id"],
        "session_id": base["session_id"],
        "run_id": base["run_id"],
        "invocation_id": base["invocation_id"],
        "id": base["id"],
        "schema_version": base["schema_version"],
    }
    assert RuntimeEvent.from_dict(base).digest() == RuntimeEvent.from_dict(reordered).digest()


def test_canonical_refs_round_trip_all_replay_boundaries():
    data = _event("function_call").to_dict()
    data["refs"] = {
        "operation_id": "operation-1",
        "step_id": "step-1",
        "provider_event_id": "provider-event-1",
        "artifact_ref": "artifact:sha256:" + "a" * 64,
        "continuation_id": "continuation-1",
    }
    event = RuntimeEvent.from_dict(data)
    assert event.to_dict()["refs"] == data["refs"]
    assert RuntimeEvent.from_dict(event.to_dict()) == event


def test_invalid_envelope_has_contract_error_not_transport_error():
    data = _event("text").to_dict()
    data["run_id"] = ""
    with pytest.raises(RuntimeEventValidationError, match="run_id"):
        RuntimeEvent.from_dict(data)

    data = _event("text").to_dict()
    data["status"] = "completed"
    data["partial"] = True
    with pytest.raises(RuntimeEventValidationError, match="partial"):
        RuntimeEvent.from_dict(data)


def test_legacy_envelope_is_rejected_without_field_inference():
    legacy = {
        "schema_version": 2,
        "id": "event",
        "kind": "text",
        "timestamp": "2026-09-05T00:00:00Z",
        "ts": 0,
        "session_id": "session",
        "turn_id": "turn",
        "run_id": "run",
        "invocation_id": "invocation",
        "text": "legacy",
        "partial": False,
        "role": "model",
        "author": "agent",
    }
    with pytest.raises(RuntimeEventValidationError, match="legacy or unknown fields"):
        RuntimeEvent.from_dict(legacy)


def test_invocation_opening_requires_protocol_route_and_source():
    data = _event("invocation_opened").to_dict()
    del data["content"]["route"]
    with pytest.raises(RuntimeEventValidationError, match="content.route"):
        RuntimeEvent.from_dict(data)

    data = _event("invocation_opened").to_dict()
    data["content"]["source"]["kind"] = "legacy"
    with pytest.raises(RuntimeEventValidationError, match="source.kind"):
        RuntimeEvent.from_dict(data)

    data = _event("invocation_opened").to_dict()
    data["partial"] = True
    with pytest.raises(RuntimeEventValidationError, match="opening event cannot be partial"):
        RuntimeEvent.from_dict(data)

    data = _event("text").to_dict()
    data["actions"] = {"end_run": True}
    with pytest.raises(RuntimeEventValidationError, match="end_run requires"):
        RuntimeEvent.from_dict(data)


def test_identity_factory_keeps_child_run_separate_from_parent():
    ids = iter(["event-1", "run-2", "invocation-2"])
    factory = IdentityFactory(token_factory=lambda: next(ids), prefix="fixture")
    parent = RunContext("session", "turn", "run-1", "invocation-1")
    child = factory.child_context(parent, branch="explore")
    assert child.parent_run_id == parent.run_id
    assert child.run_id != parent.run_id
    assert child.invocation_id != parent.invocation_id


def test_redaction_and_bounded_reference_never_expose_secret():
    clean = redact_payload(
        {
            "api_key": "sk-ant-super-secret-value",
            "nested": {"Authorization": "Bearer abcdefghijklmnop"},
            "result": "x" * 30,
        },
        RedactionPolicy(max_string_chars=10),
    )
    assert clean["api_key"] == "[REDACTED]"
    assert clean["nested"]["Authorization"] == "[REDACTED]"
    assert clean["result"]["kind"] == "bounded_ref"
    assert "sk-ant-super-secret-value" not in repr(clean)
    reference = bound_payload("x" * 50, ref="artifact-1", policy=RedactionPolicy(max_inline_bytes=4))
    assert reference["truncated"] is True
    assert reference["sha256"].startswith("sha256:")


def test_redaction_keeps_long_canonical_text_string_for_replay():
    text = "reasoning " * 2_000
    clean, report = redact_event_dict(
        {
            "content": {"kind": "thinking", "text": text},
        },
        RedactionPolicy(max_string_chars=10),
    )

    assert clean["content"]["text"] == text
    assert isinstance(clean["content"]["text"], str)
    assert "content.text" not in report.bounded_paths


def test_emitter_redacts_before_canonical_sink():
    canonical = RecordingEventSink()

    source = _event("function_call").to_dict()
    source["content"]["args"] = {"api_key": "sk-ant-hidden"}
    emitted = RuntimeEventEmitter(
        CanonicalSink(canonical),
    ).emit(source)
    assert emitted.content["args"]["api_key"] == "[REDACTED]"
    assert canonical.events[0] == emitted


def test_canonical_failure_is_fail_closed():
    def fail(point: str, event: RuntimeEvent) -> None:
        del event
        if point == "emit":
            raise OSError("canonical unavailable")

    canonical = CanonicalSink(RecordingEventSink(failure_hook=fail))
    with pytest.raises(CanonicalSinkError, match="canonical unavailable"):
        canonical.emit(_event("text"))
