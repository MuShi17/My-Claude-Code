"""Non-silent integration probes reserved for C06-C11."""

from __future__ import annotations

def test_projection_modules_are_real_importable_capabilities():
    from mini_claude.projections.model_replay_projection import ModelReplayProjection
    from mini_claude.projections.run_trace_projection import RunTraceProjection
    from mini_claude.projections.session_projection import SessionProjection

    assert all((SessionProjection, ModelReplayProjection, RunTraceProjection))


def test_recovery_module_is_importable():
    from mini_claude.recovery import RecoveryProjection

    assert RecoveryProjection is not None


def test_shadow_parity_module_is_importable():
    from mini_claude.shadow_parity import ShadowParityReport

    assert ShadowParityReport is not None


def test_legacy_shadow_sink_is_importable():
    from mini_claude.event_sink import LegacyShadowSink

    assert LegacyShadowSink is not None
