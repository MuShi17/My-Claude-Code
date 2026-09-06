"""Verifiable, bounded compaction checkpoints derived from event prefixes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .projections.base import EventRecord, PROJECTION_VERSION, RuntimeEventReducer, iter_event_records, source_digest
from .runtime_event import RuntimeEvent, SCHEMA_VERSION, canonical_json_bytes

COMPACTION_SCHEMA_VERSION = 1
COMPACTION_VERSION = "compaction-v1"


class CompactionError(RuntimeError):
    code = "compaction_error"


class CheckpointSourceMismatchError(CompactionError):
    code = "checkpoint_source_mismatch"


class CheckpointCoverageError(CompactionError):
    code = "checkpoint_coverage_error"


@dataclass(frozen=True, slots=True)
class CompactionCheckpoint:
    checkpoint_id: str
    source_high_water: int
    source_digest: str
    canonical_schema_version: int
    compaction_version: str
    projection_version: str
    coverage: Mapping[str, Any]
    summary: Mapping[str, Any]
    recent_tail: tuple[Mapping[str, Any], ...]
    created_at: str
    schema_version: int = COMPACTION_SCHEMA_VERSION
    context_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "source_high_water": self.source_high_water,
            "source_digest": self.source_digest,
            "canonical_schema_version": self.canonical_schema_version,
            "compaction_version": self.compaction_version,
            "projection_version": self.projection_version,
            "coverage": dict(self.coverage),
            "summary": dict(self.summary),
            "recent_tail": [dict(item) for item in self.recent_tail],
            "created_at": self.created_at,
        }
        if self.context_id is not None:
            result["context_id"] = self.context_id
        return result

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompactionCheckpoint":
        try:
            checkpoint = cls(
                checkpoint_id=str(value["checkpoint_id"]),
                source_high_water=int(value["source_high_water"]),
                source_digest=str(value["source_digest"]),
                canonical_schema_version=int(value["canonical_schema_version"]),
                compaction_version=str(value["compaction_version"]),
                projection_version=str(value["projection_version"]),
                coverage=dict(value["coverage"]),
                summary=dict(value.get("summary") or {}),
                recent_tail=tuple(dict(item) for item in value.get("recent_tail", [])),
                created_at=str(value["created_at"]),
                context_id=(
                    str(value["context_id"])
                    if value.get("context_id") is not None
                    else None
                ),
                schema_version=int(value.get("schema_version", COMPACTION_SCHEMA_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CompactionError(f"invalid compaction checkpoint: {error}") from error
        if checkpoint.schema_version != COMPACTION_SCHEMA_VERSION:
            raise CompactionError(f"unsupported checkpoint schema {checkpoint.schema_version}")
        return checkpoint


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _default_summary(records: list[EventRecord]) -> dict[str, Any]:
    reducer = RuntimeEventReducer(records)
    kinds: dict[str, int] = {}
    for record in records:
        kind = record.event.kind or "unknown"
        kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "event_count": len(records),
        "kind_counts": dict(sorted(kinds.items())),
        "tool_call_count": len(reducer.calls),
        "diagnostic_count": len(reducer.diagnostics),
        "terminal_count": len(reducer.terminals),
    }


class CompactionCheckpointBuilder:
    """Build and verify checkpoints from an immutable ordinal prefix."""

    def __init__(
        self,
        *,
        compaction_version: str = COMPACTION_VERSION,
        projection_version: str = PROJECTION_VERSION,
        canonical_schema_version: int = SCHEMA_VERSION,
        recent_tail_events: int = 12,
        recent_tail_bytes: int = 16 * 1024,
    ) -> None:
        if recent_tail_events < 0 or recent_tail_bytes < 1:
            raise ValueError("checkpoint bounds must be non-negative/positive")
        self.compaction_version = compaction_version
        self.projection_version = projection_version
        self.canonical_schema_version = canonical_schema_version
        self.recent_tail_events = recent_tail_events
        self.recent_tail_bytes = recent_tail_bytes

    def build(
        self,
        source: Any,
        *,
        high_water: int | None = None,
        checkpoint_id: str | None = None,
        coverage: Mapping[str, Any] | None = None,
        summary: Mapping[str, Any] | str | None = None,
        created_at: str | None = None,
        context_id: str | None = None,
    ) -> CompactionCheckpoint:
        records = iter_event_records(
            source, high_water=high_water, context_id=context_id
        )
        records.sort(key=lambda item: item.ordinal)
        requested = high_water
        if requested is None:
            requested = max((item.ordinal for item in records), default=0)
        if requested < 0:
            raise CheckpointCoverageError("source high-water must not be negative")
        if records and records[-1].ordinal > requested:
            raise CheckpointCoverageError("checkpoint includes an event above source high-water")
        if requested and context_id is None and (not records or records[-1].ordinal != requested):
            raise CheckpointCoverageError(
                f"checkpoint source does not cover requested high-water {requested}"
            )
        actual_digest = source_digest(records)
        first = records[0].ordinal if records else 0
        explicit_coverage = dict(coverage or {})
        explicit_coverage.setdefault("from_ordinal", first)
        explicit_coverage.setdefault("to_ordinal", requested)
        explicit_coverage.setdefault("event_count", len(records))
        explicit_coverage.setdefault("complete", True)
        if explicit_coverage["to_ordinal"] != requested:
            raise CheckpointCoverageError("coverage to_ordinal must equal source high-water")

        if summary is None:
            summary_value: dict[str, Any] = _default_summary(records)
        elif isinstance(summary, str):
            summary_value = {"text": summary}
        else:
            summary_value = dict(summary)
        summary_value.setdefault("coverage", {"from_ordinal": first, "to_ordinal": requested})

        tail: list[Mapping[str, Any]] = []
        used = 0
        for record in reversed(records[-self.recent_tail_events :] if self.recent_tail_events else []):
            item = record.event.to_dict()
            encoded_size = len(canonical_json_bytes(item))
            if used + encoded_size > self.recent_tail_bytes:
                break
            tail.append(item)
            used += encoded_size
        tail.reverse()
        digest_seed = f"{requested}:{actual_digest}:{self.compaction_version}".encode()
        checkpoint_key = checkpoint_id or "checkpoint:" + hashlib.sha256(digest_seed).hexdigest()[:24]
        return CompactionCheckpoint(
            checkpoint_id=checkpoint_key,
            source_high_water=requested,
            source_digest=actual_digest,
            canonical_schema_version=self.canonical_schema_version,
            compaction_version=self.compaction_version,
            projection_version=self.projection_version,
            coverage=explicit_coverage,
            summary=summary_value,
            recent_tail=tuple(tail),
            created_at=created_at or _utc_now(),
            context_id=context_id,
        )

    def verify(self, checkpoint: CompactionCheckpoint | Mapping[str, Any], source: Any) -> None:
        expected = checkpoint if isinstance(checkpoint, CompactionCheckpoint) else CompactionCheckpoint.from_dict(checkpoint)
        rebuilt = self.build(
            source,
            high_water=expected.source_high_water,
            context_id=expected.context_id,
        )
        if rebuilt.source_high_water != expected.source_high_water or rebuilt.source_digest != expected.source_digest:
            raise CheckpointSourceMismatchError(
                f"checkpoint {expected.checkpoint_id} does not match source prefix"
            )
        if dict(expected.coverage).get("to_ordinal") != expected.source_high_water:
            raise CheckpointCoverageError("checkpoint claims coverage beyond its source high-water")

    def rebuild(self, checkpoint: CompactionCheckpoint | Mapping[str, Any], source: Any) -> CompactionCheckpoint:
        expected = checkpoint if isinstance(checkpoint, CompactionCheckpoint) else CompactionCheckpoint.from_dict(checkpoint)
        rebuilt = self.build(
            source,
            high_water=expected.source_high_water,
            checkpoint_id=expected.checkpoint_id,
            coverage=expected.coverage,
            summary=expected.summary,
            created_at=expected.created_at,
            context_id=expected.context_id,
        )
        self.verify(expected, source)
        return rebuilt


build_compaction_checkpoint = CompactionCheckpointBuilder().build


__all__ = [
    "COMPACTION_SCHEMA_VERSION",
    "COMPACTION_VERSION",
    "CheckpointCoverageError",
    "CheckpointSourceMismatchError",
    "CompactionCheckpoint",
    "CompactionCheckpointBuilder",
    "CompactionError",
    "build_compaction_checkpoint",
]
