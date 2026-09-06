"""Durable effective-context transition contract."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .runtime_event import canonical_json_bytes, thaw


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
    context_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
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
        if self.context_id is not None:
            result["context_id"] = self.context_id
        return result

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
            context_id=(
                _text(value["context_id"], "context_id")
                if value.get("context_id") is not None
                else None
            ),
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
    context_id: str | None = None,
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
        context_id=(
            _text(context_id, "context_id") if context_id is not None else None
        ),
    )


@dataclass(frozen=True, slots=True)
class TransitionValidationResult:
    """The effective context produced by a side-effect-free validation."""

    messages: tuple[dict[str, Any], ...]
    result_digest: str
    context_epoch: str


def _validate_tool_message_groups(messages: list[dict[str, Any]]) -> None:
    """Reject provider-invalid orphaned or incomplete tool message groups."""

    pending: set[str] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            if pending:
                raise ContextTransitionError(
                    f"tool call group at message {index} starts before prior results"
                )
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                raise ContextTransitionError("assistant tool_calls must be a non-empty list")
            ids: list[str] = []
            for call in calls:
                if not isinstance(call, Mapping) or not isinstance(call.get("id"), str):
                    raise ContextTransitionError("tool call must have a string id")
                call_id = call["id"]
                if not call_id or call_id in ids:
                    raise ContextTransitionError("tool call ids must be unique within a group")
                ids.append(call_id)
            pending.update(ids)
            continue
        if role == "tool":
            if not pending:
                raise ContextTransitionError(
                    f"tool result at message {index} has no preceding tool call group"
                )
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ContextTransitionError(
                    f"tool result at message {index} does not match its call group"
                )
            pending.remove(call_id)
            continue
        if pending:
            raise ContextTransitionError(
                f"tool call group is incomplete before message {index}"
            )
    if pending:
        raise ContextTransitionError("tool call group has no corresponding results")


def validate_transition_candidate(
    messages: Iterable[Mapping[str, Any]],
    transition: ContextTransition | Mapping[str, Any],
    *,
    source_high_water: int,
    source_digest: str,
    expected_projection_version: str | None = None,
    expected_policy_version: str | None = None,
    current_context_epoch: str | None = None,
    context_id: str | None = None,
    reset_context: Iterable[Mapping[str, Any]] | None = None,
) -> TransitionValidationResult:
    """Validate and apply a transition to a copied neutral context.

    This function deliberately has no store or provider side effects.  Event
    identity is authoritative: ``target_call_id`` only confirms the exact
    message selected by ``target_event_id`` and is never used as a fallback
    lookup key.
    """

    candidate = (
        transition
        if isinstance(transition, ContextTransition)
        else ContextTransition.from_value(transition)
    )
    if candidate.source_high_water != source_high_water:
        raise ContextTransitionError("transition source high-water differs from active prefix")
    if candidate.source_digest != source_digest:
        raise ContextTransitionError("transition source digest differs from active prefix")
    if (
        expected_projection_version is not None
        and candidate.projection_version != expected_projection_version
    ):
        raise ContextTransitionError("transition projection version is unsupported")
    if (
        expected_policy_version is not None
        and candidate.policy_version != expected_policy_version
    ):
        raise ContextTransitionError("transition policy version is unsupported")
    if context_id is not None and candidate.context_id not in {None, context_id}:
        raise ContextTransitionError("transition context identity does not match active context")

    reset_messages: list[dict[str, Any]] | None = None
    if reset_context is not None:
        reset_values = list(reset_context)
        if any(not isinstance(message, Mapping) for message in reset_values):
            raise ContextTransitionError("reset context messages must be objects")
        reset_messages = [dict(deepcopy(thaw(message))) for message in reset_values]
        _validate_tool_message_groups(reset_messages)
        expected_digest = replacement_digest(reset_messages)
    else:
        candidate_messages = [dict(deepcopy(thaw(message))) for message in messages]
        _validate_tool_message_groups(candidate_messages)
        expected_digest = None
        if candidate.replacements:
            expected_digest = replacement_digest(
                {"replacements": [item.to_dict() for item in candidate.replacements]}
            )

    if current_context_epoch is not None and reset_context is None:
        if candidate.context_epoch != current_context_epoch:
            raise ContextTransitionError(
                "non-reset transition cannot change context epoch"
            )

    if reset_messages is not None:
        result_messages = reset_messages
    else:
        result_messages = candidate_messages
        seen_targets: set[str] = set()
        for replacement in candidate.replacements:
            if replacement.target_event_id in seen_targets:
                raise ContextTransitionError(
                    f"transition target repeated: {replacement.target_event_id}"
                )
            seen_targets.add(replacement.target_event_id)
            matches = [
                message
                for message in result_messages
                if message.get("runtime_event_id") == replacement.target_event_id
            ]
            if not matches:
                raise ContextTransitionError(
                    f"transition target not found: {replacement.target_event_id}"
                )
            if len(matches) != 1:
                raise ContextTransitionError(
                    f"transition target is ambiguous: {replacement.target_event_id}"
                )
            target = matches[0]
            if (
                replacement.target_call_id is not None
                and target.get("tool_call_id") != replacement.target_call_id
            ):
                raise ContextTransitionError(
                    f"transition target identity mismatch: {replacement.target_event_id}"
                )
            target["content"] = deepcopy(thaw(replacement.replacement))
        _validate_tool_message_groups(result_messages)

    if expected_digest is None:
        expected_digest = replacement_digest(result_messages)
    if candidate.result_digest != expected_digest:
        raise ContextTransitionError("transition result digest mismatch")
    return TransitionValidationResult(
        messages=tuple(result_messages),
        result_digest=expected_digest,
        context_epoch=candidate.context_epoch,
    )


__all__ = [
    "CONTEXT_TRANSITION_VERSION",
    "ContextReplacement",
    "ContextTransition",
    "ContextTransitionError",
    "TransitionValidationResult",
    "build_context_transition",
    "replacement_digest",
    "validate_transition_candidate",
]
