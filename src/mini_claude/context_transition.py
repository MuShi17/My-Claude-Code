"""Durable effective-context transition contract."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .runtime_event import canonical_json_bytes


CONTEXT_TRANSITION_VERSION = 1


class ContextTransitionError(ValueError):
    """A context transition cannot be safely validated or replayed."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextTransitionError(f"{field} must be a non-empty string")
    return value


def replacement_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextReplacement:
    target_event_id: str
    replacement: Any
    reason: str
    target_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "target_event_id": self.target_event_id,
            "replacement": self.replacement,
            "replacement_digest": replacement_digest(self.replacement),
            "reason": self.reason,
        }
        if self.target_call_id is not None:
            result["target_call_id"] = self.target_call_id
        return result

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "ContextReplacement":
        target_event_id = _text(value.get("target_event_id"), "target_event_id")
        reason = _text(value.get("reason"), "reason")
        if "replacement" not in value:
            raise ContextTransitionError("replacement is required")
        replacement = value["replacement"]
        expected = value.get("replacement_digest")
        if expected is not None and expected != replacement_digest(replacement):
            raise ContextTransitionError("replacement digest mismatch")
        target_call_id = value.get("target_call_id")
        if target_call_id is not None:
            target_call_id = _text(target_call_id, "target_call_id")
        return cls(target_event_id, replacement, reason, target_call_id)


@dataclass(frozen=True, slots=True)
class ContextTransition:
    source_high_water: int
    source_digest: str
    projection_version: str
    policy_version: str
    context_epoch: str
    reason: str
    replacements: tuple[ContextReplacement, ...]
    result_digest: str
    version: int = CONTEXT_TRANSITION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_high_water": self.source_high_water,
            "source_digest": self.source_digest,
            "projection_version": self.projection_version,
            "policy_version": self.policy_version,
            "context_epoch": self.context_epoch,
            "reason": self.reason,
            "replacements": [item.to_dict() for item in self.replacements],
            "result_digest": self.result_digest,
        }

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "ContextTransition":
        try:
            version = int(value.get("version", CONTEXT_TRANSITION_VERSION))
            source_high_water = int(value["source_high_water"])
            replacements_value = value.get("replacements", [])
        except (KeyError, TypeError, ValueError) as error:
            raise ContextTransitionError(f"invalid transition fields: {error}") from error
        if version != CONTEXT_TRANSITION_VERSION:
            raise ContextTransitionError(f"unsupported transition version {version}")
        if source_high_water < 0 or not isinstance(replacements_value, (list, tuple)):
            raise ContextTransitionError("invalid transition source or replacements")
        parsed_replacements: list[ContextReplacement] = []
        for item in replacements_value:
            if not isinstance(item, Mapping):
                raise ContextTransitionError("replacement must be an object")
            parsed_replacements.append(ContextReplacement.from_value(item))
        return cls(
            source_high_water=source_high_water,
            source_digest=_text(value.get("source_digest"), "source_digest"),
            projection_version=_text(value.get("projection_version"), "projection_version"),
            policy_version=_text(value.get("policy_version"), "policy_version"),
            context_epoch=_text(value.get("context_epoch"), "context_epoch"),
            reason=_text(value.get("reason"), "reason"),
            replacements=tuple(parsed_replacements),
            result_digest=_text(value.get("result_digest"), "result_digest"),
            version=version,
        )


def build_context_transition(
    *,
    source_high_water: int,
    source_digest: str,
    projection_version: str,
    policy_version: str,
    context_epoch: str,
    reason: str,
    replacements: list[ContextReplacement] | tuple[ContextReplacement, ...],
    effective_context: Any,
) -> ContextTransition:
    return ContextTransition(
        source_high_water=source_high_water,
        source_digest=_text(source_digest, "source_digest"),
        projection_version=_text(projection_version, "projection_version"),
        policy_version=_text(policy_version, "policy_version"),
        context_epoch=_text(context_epoch, "context_epoch"),
        reason=_text(reason, "reason"),
        replacements=tuple(replacements),
        result_digest=replacement_digest(effective_context),
    )


__all__ = [
    "CONTEXT_TRANSITION_VERSION",
    "ContextReplacement",
    "ContextTransition",
    "ContextTransitionError",
    "build_context_transition",
    "replacement_digest",
]
