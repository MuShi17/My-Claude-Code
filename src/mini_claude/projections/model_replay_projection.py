"""Provider-neutral model-history projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .base import (
    PROJECTION_VERSION,
    ProjectionDiagnostic,
    RuntimeEventReducer,
    iter_event_records,
    json_value,
    source_digest,
    stable_digest,
)


@dataclass(frozen=True, slots=True)
class ModelReplayResult:
    projection_version: str
    schema_version: int
    high_water: int
    source_digest: str
    digest: str
    messages: tuple[dict[str, Any], ...]
    partial_count: int
    diagnostics: tuple[ProjectionDiagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_version": self.projection_version,
            "schema_version": self.schema_version,
            "high_water": self.high_water,
            "source_digest": self.source_digest,
            "digest": self.digest,
            "messages": list(self.messages),
            "partial_count": self.partial_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class ModelReplayProjection:
    projection_version = PROJECTION_VERSION

    def project(self, source: Any, *, high_water: int | None = None) -> ModelReplayResult:
        records = iter_event_records(source, high_water=high_water)
        reducer = RuntimeEventReducer(records)
        messages: list[dict[str, Any]] = []
        call_records = sorted(reducer.calls.items(), key=lambda item: item[1].ordinal)
        calls_by_event_id = {record.event.id: (key, record) for key, record in call_records}
        response_event_ids = {
            response.event.id
            for values in reducer.responses.values()
            for response in values
        }
        for record in records:
            event = record.event
            if event.partial or event.model_visibility == "hidden":
                continue
            compaction = (event.actions or {}).get("compaction")
            if isinstance(compaction, Mapping) and compaction.get("reset_model_context"):
                messages.clear()
                for message in compaction.get("context_messages", []):
                    if isinstance(message, Mapping) and message.get("role") in {
                        "user", "assistant", "tool"
                    }:
                        item = dict(message)
                        item["runtime_event_id"] = event.id
                        messages.append(item)
                continue
            content = event.content or {}
            kind = content.get("kind")
            if kind == "text" and event.role in {"user", "model"} and content.get("text"):
                messages.append(
                    {
                        "role": "user" if event.role == "user" else "assistant",
                        "content": content["text"],
                        "runtime_event_id": event.id,
                    }
                )
            elif kind == "thinking" and event.role == "model":
                item: dict[str, Any] = {
                    "kind": "thinking",
                    "text": content.get("text", ""),
                    "runtime_event_id": event.id,
                }
                if content.get("signature"):
                    item["signature"] = content["signature"]
                messages.append({"role": "assistant", "content": [item]})
            elif kind == "function_call":
                key_record = calls_by_event_id.get(event.id)
                if key_record is None:
                    continue
                key, _ = key_record
                response = reducer.response_for(*key)
                if response is None:
                    # The call remains a diagnostic/unsettled fact and must
                    # not become an executable provider message.
                    continue
                messages.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": content.get("id"),
                                "name": content.get("name"),
                                "arguments": json_value(content.get("args")),
                            }
                        ],
                        "runtime_event_id": event.id,
                    }
                )
            elif (
                kind == "function_response"
                and event.id in response_event_ids
                and (event.run_id, str(content.get("id", ""))) in reducer.calls
                and record.ordinal
                > reducer.calls[(event.run_id, str(content.get("id", "")))].ordinal
                and event.kind != "tool_outcome"
                and (event.metadata or {}).get("lifecycle") != "tool_outcome"
            ):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": content.get("id"),
                        "content": json_value(content.get("result")),
                        "runtime_event_id": event.id,
                    }
                )
        output = {"messages": messages, "partial_count": len(reducer.partial)}
        return ModelReplayResult(
            projection_version=self.projection_version,
            schema_version=1,
            high_water=high_water if high_water is not None else (records[-1].ordinal if records else 0),
            source_digest=source_digest(records),
            digest=stable_digest(output),
            messages=tuple(messages),
            partial_count=len(reducer.partial),
            diagnostics=tuple(reducer.diagnostics),
        )

    build = project


__all__ = ["ModelReplayProjection", "ModelReplayResult"]
