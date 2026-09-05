"""Final canonical/legacy parity and authority-cutover gates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

from .event_sink import CompositeEventSink, EventSink

AuthorityMode = Literal["legacy", "shadow", "canonical"]


class CutoverBlockedError(RuntimeError):
    code = "cutover_blocked"


@dataclass(frozen=True, slots=True)
class ParityMismatch:
    scenario: str
    classification: Literal["blocker", "allowed", "remaining-gap"]
    path: str
    message: str
    left: Any = None
    right: Any = None
    owner: str = "runtime-logging"
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "classification": self.classification,
            "path": self.path,
            "message": self.message,
            "left": self.left,
            "right": self.right,
            "owner": self.owner,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ParityReport:
    scenario: str
    equal: bool
    mismatches: tuple[ParityMismatch, ...]
    stable_left: Any
    stable_right: Any
    before: Any | None = None
    after: Any | None = None

    @property
    def blockers(self) -> tuple[ParityMismatch, ...]:
        return tuple(item for item in self.mismatches if item.classification == "blocker")

    @property
    def allowed_differences(self) -> tuple[ParityMismatch, ...]:
        return tuple(item for item in self.mismatches if item.classification == "allowed")

    @property
    def remaining_gaps(self) -> tuple[ParityMismatch, ...]:
        return tuple(item for item in self.mismatches if item.classification == "remaining-gap")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "equal": self.equal,
            "mismatches": [item.to_dict() for item in self.mismatches],
            "blocker_count": len(self.blockers),
            "allowed_difference_count": len(self.allowed_differences),
            "remaining_gap_count": len(self.remaining_gaps),
            "stable_left": self.stable_left,
            "stable_right": self.stable_right,
            "before": self.before,
            "after": self.after,
        }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _event_kind(value: Mapping[str, Any]) -> str:
    if value.get("kind"):
        return str(value["kind"])
    content = value.get("content")
    if isinstance(content, Mapping) and content.get("kind"):
        return str(content["kind"])
    metadata = value.get("metadata") or value.get("runtime_metadata")
    if isinstance(metadata, Mapping) and metadata.get("lifecycle"):
        return str(metadata["lifecycle"])
    for name in ("tool_dispatch", "tool_outcome", "permission", "compaction"):
        if name in value.get("actions", {}):
            return name
    return str(value.get("runtime_kind", "unknown"))


def _stable_value(value: Any, *, key: str = "", identities: dict[tuple[str, str], str] | None = None) -> Any:
    identities = identities if identities is not None else {}
    value = _json_safe(value)
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for name, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            name = str(name)
            if name in {
                "timestamp", "ts", "created_at", "latency_ms", "provider", "model",
                "runtime_metadata", "metadata", "source_digest", "digest",
            }:
                continue
            if name in {"id", "event_id", "request_id", "session_id", "turn_id", "run_id", "invocation_id", "call_id", "tool_call_id", "canonical_event_id"} or name.endswith("_id"):
                text = str(item)
                identity_key = (name, text)
                identities.setdefault(identity_key, f"<{name}:{len(identities) + 1}>")
                output[name] = identities[identity_key]
            else:
                output[name] = _stable_value(item, key=name, identities=identities)
        return output
    if isinstance(value, (list, tuple)):
        return [_stable_value(item, key=key, identities=identities) for item in value]
    if isinstance(value, str):
        return value.replace("\\", "/")
    return value


def stable_semantic_projection(value: Any) -> Any:
    """Normalise nondeterministic envelope fields while retaining event facts."""

    value = _json_safe(value)
    if isinstance(value, list):
        projected: list[Any] = []
        identities: dict[tuple[str, str], str] = {}
        for item in value:
            if isinstance(item, Mapping) and (
                "kind" in item
                or "runtime_kind" in item
                or "canonical_event_id" in item
                or isinstance(item.get("content"), Mapping)
                or "actions" in item
                or "refs" in item
            ):
                content = item.get("content") if isinstance(item.get("content"), Mapping) else {}
                actions = item.get("actions") if isinstance(item.get("actions"), Mapping) else {}
                metadata = item.get("metadata") or item.get("runtime_metadata")
                lifecycle = metadata.get("lifecycle") if isinstance(metadata, Mapping) else None
                kind = _event_kind(item)
                semantic: dict[str, Any] = {
                    "kind": kind,
                    "partial": bool(item.get("partial", False)),
                    "role": item.get("role"),
                    "author": item.get("author"),
                    "status": item.get("status"),
                }
                if lifecycle:
                    semantic["lifecycle"] = lifecycle
                for name in ("content", "actions", "refs"):
                    source = item.get(name)
                    if source:
                        semantic[name] = _stable_value(source, identities=identities)
                if "call_id" in item:
                    semantic["call_id"] = _stable_value(item["call_id"], key="call_id", identities=identities)
                projected.append(semantic)
            else:
                projected.append(_stable_value(item, identities=identities))
        return projected
    return _stable_value(value)


def _diff(left: Any, right: Any, *, path: str, scenario: str, evidence: tuple[str, ...]) -> list[ParityMismatch]:
    if type(left) is not type(right):
        return [ParityMismatch(scenario, "blocker", path, "stable semantic types differ", left, right, evidence=evidence)]
    if isinstance(left, Mapping):
        result: list[ParityMismatch] = []
        keys = sorted(set(left) | set(right))
        for key in keys:
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                classification = "allowed" if key in {"provider", "model", "timestamp", "ts", "created_at"} else "blocker"
                result.append(ParityMismatch(scenario, classification, child, "semantic field missing on one side", left.get(key), right.get(key), evidence=evidence))
            else:
                result.extend(_diff(left[key], right[key], path=child, scenario=scenario, evidence=evidence))
        return result
    if isinstance(left, list):
        result = []
        if len(left) != len(right):
            result.append(ParityMismatch(scenario, "blocker", path, "event/message count differs", len(left), len(right), evidence=evidence))
        for index, (l_item, r_item) in enumerate(zip(left, right)):
            result.extend(_diff(l_item, r_item, path=f"{path}[{index}]", scenario=scenario, evidence=evidence))
        return result
    if left != right:
        return [ParityMismatch(scenario, "blocker", path, "stable semantic value differs", left, right, evidence=evidence)]
    return []


def _allowed_diff(left: Any, right: Any, *, path: str, scenario: str, evidence: tuple[str, ...]) -> list[ParityMismatch]:
    """Report intentionally ignored envelope/provider differences separately."""

    left = _json_safe(left)
    right = _json_safe(right)
    allowed_keys = {"provider", "model", "timestamp", "ts", "created_at", "latency_ms"}
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        result: list[ParityMismatch] = []
        for key in sorted(set(left) & set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key in allowed_keys and left[key] != right[key]:
                result.append(ParityMismatch(scenario, "allowed", child, "provider/non-deterministic metadata differs", left[key], right[key], evidence=evidence))
            else:
                result.extend(_allowed_diff(left[key], right[key], path=child, scenario=scenario, evidence=evidence))
        return result
    if isinstance(left, list) and isinstance(right, list):
        result: list[ParityMismatch] = []
        for index, (l_item, r_item) in enumerate(zip(left, right)):
            result.extend(_allowed_diff(l_item, r_item, path=f"{path}[{index}]", scenario=scenario, evidence=evidence))
        return result
    return []


class StableSemanticComparator:
    version = "stable-semantic-v1"

    def compare(
        self,
        left: Any,
        right: Any,
        *,
        scenario: str = "unnamed",
        evidence: Iterable[str] = (),
        before: Any | None = None,
        after: Any | None = None,
    ) -> ParityReport:
        stable_left = stable_semantic_projection(left)
        stable_right = stable_semantic_projection(right)
        evidence = tuple(evidence)
        mismatches = tuple(
            _diff(stable_left, stable_right, path="", scenario=scenario, evidence=evidence)
            + _allowed_diff(left, right, path="", scenario=scenario, evidence=evidence)
        )
        return ParityReport(scenario, not any(item.classification == "blocker" for item in mismatches), mismatches, stable_left, stable_right, before, after)

    compare_events = compare


@dataclass
class GapRegister:
    entries: list[ParityMismatch] = field(default_factory=list)

    def add(self, mismatch: ParityMismatch) -> None:
        self.entries.append(mismatch)

    def add_report(self, report: ParityReport) -> None:
        self.entries.extend(report.mismatches)

    @property
    def blockers(self) -> tuple[ParityMismatch, ...]:
        return tuple(item for item in self.entries if item.classification == "blocker")

    def close(self, *, scenario: str, path: str) -> None:
        self.entries = [item for item in self.entries if not (item.scenario == scenario and item.path == path)]

    def to_dict(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.entries]

    def to_markdown(self) -> str:
        lines = ["# Canonical Log Gap Register", "", "| Scenario | Classification | Path | Owner | Evidence |", "|---|---|---|---|---|"]
        for item in self.entries:
            lines.append(f"| {item.scenario} | {item.classification} | `{item.path}` | {item.owner} | {', '.join(item.evidence) or '-'} |")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class AuthorityConfig:
    mode: AuthorityMode = "shadow"
    rollback: bool = False
    shadow_enabled: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"legacy", "shadow", "canonical"}:
            raise ValueError(f"unsupported authority mode {self.mode!r}")


class AuthorityGate:
    """Require explicit evidence before canonical authority is selected."""

    def choose(self, config: AuthorityConfig, *, reports: Iterable[ParityReport] = (), gaps: GapRegister | None = None) -> AuthorityMode:
        if config.rollback:
            return "legacy"
        blockers = [item for report in reports for item in report.blockers]
        if gaps is not None:
            blockers.extend(gaps.blockers)
        if config.mode == "canonical" and blockers:
            raise CutoverBlockedError(
                "canonical authority is blocked by " + "; ".join(f"{item.scenario}:{item.path}" for item in blockers)
            )
        return config.mode

    def require_approved(self, config: AuthorityConfig, *, approved: bool, reports: Iterable[ParityReport] = (), gaps: GapRegister | None = None) -> AuthorityMode:
        if config.mode == "canonical" and not approved and not config.rollback:
            raise CutoverBlockedError("canonical authority requires explicit approval and acceptance evidence")
        return self.choose(config, reports=reports, gaps=gaps)


def select_event_sink(
    canonical: EventSink,
    legacy: EventSink | None,
    config: AuthorityConfig,
    *,
    approved: bool = False,
    reports: Iterable[ParityReport] = (),
    gaps: GapRegister | None = None,
) -> EventSink:
    mode = AuthorityGate().require_approved(config, approved=approved, reports=reports, gaps=gaps)
    if mode == "legacy" and legacy is not None:
        return legacy
    if mode == "canonical" or legacy is None:
        return canonical
    return CompositeEventSink(canonical, [legacy])


def render_acceptance_report(
    reports: Iterable[ParityReport],
    *,
    gaps: GapRegister | None = None,
    commands: Iterable[str] = (),
    test_results: Iterable[str] = (),
    authority: str = "shadow",
) -> str:
    reports = tuple(reports)
    gap_entries = tuple(gaps.entries if gaps else ())
    lines = [
        "# Agent Log Canonical Runtime Event Acceptance Report",
        "",
        f"- Comparator: `{StableSemanticComparator.version}`",
        f"- Tested route: `{authority}`",
        f"- Blockers: `{sum(len(report.blockers) for report in reports) + sum(item.classification == 'blocker' for item in gap_entries)}`",
        "- Data policy: runtime.sqlite, legacy JSONL/session/traces/llm and artifacts are retained; no cleanup or canonical rewrite.",
        "- Authority note: formal canonical cutover remains an explicit approval action.",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Equal | Blockers | Allowed | Remaining gaps |",
        "|---|---:|---:|---:|---:|",
    ]
    for report in reports:
        lines.append(f"| {report.scenario} | {'yes' if report.equal else 'no'} | {len(report.blockers)} | {len(report.allowed_differences)} | {len(report.remaining_gaps)} |")
    lines.extend(["", "## Commands", ""])
    lines.extend(f"- `{command}`" for command in commands)
    lines.extend(["", "## Test Results", ""])
    lines.extend(f"- {result}" for result in test_results)
    if gap_entries:
        lines.extend(["", "## Gap Register", "", "| Scenario | Classification | Path | Owner |", "|---|---|---|---|"])
        lines.extend(f"| {item.scenario} | {item.classification} | `{item.path}` | {item.owner} |" for item in gap_entries)
    lines.extend(["", "## Cutover Decision", "", "Canonical authority is not enabled by this report. Use `AuthorityGate` with explicit approval after all blockers are closed.", ""])
    return "\n".join(lines)


__all__ = [
    "AuthorityConfig",
    "AuthorityGate",
    "CutoverBlockedError",
    "GapRegister",
    "ParityMismatch",
    "ParityReport",
    "StableSemanticComparator",
    "render_acceptance_report",
    "select_event_sink",
    "stable_semantic_projection",
]
