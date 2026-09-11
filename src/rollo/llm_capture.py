"""Explicit privacy modes for bounded provider request/response capture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from .artifact_archive import ArtifactArchive, ArtifactArchiveError, ArtifactRef
from .redaction import RedactionPolicy, RedactionReport, redact_payload
from .runtime_event import canonical_json_bytes

CaptureMode = Literal["off", "metadata-only", "redacted"]


class LLMCaptureError(RuntimeError):
    code = "llm_capture_error"


@dataclass(frozen=True, slots=True)
class LLMCapturePolicy:
    mode: CaptureMode = "off"
    max_body_bytes: int = 16 * 1024
    archive_bodies: bool = True
    redaction_policy: RedactionPolicy = field(default_factory=RedactionPolicy)

    def __post_init__(self) -> None:
        if self.mode not in {"off", "metadata-only", "redacted"}:
            raise ValueError(f"unsupported LLM capture mode {self.mode!r}")
        if self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")


@dataclass(frozen=True, slots=True)
class LLMCaptureResult:
    capture_status: str
    llm_ref: str | None
    metadata: Mapping[str, Any]
    error: str | None = None

    @property
    def saved(self) -> bool:
        return self.capture_status in {"saved", "metadata-only", "off"}

    def to_dict(self) -> dict[str, Any]:
        value = {
            "capture_status": self.capture_status,
            "llm_ref": self.llm_ref,
            "metadata": dict(self.metadata),
        }
        if self.error:
            value["error"] = self.error
        return value


def _shape(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _shape(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_shape(item) for item in value]
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def request_shape_hash(request: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(_shape(request))).hexdigest()


def _body(value: Any) -> tuple[bytes, RedactionReport, Any]:
    if isinstance(value, bytes):
        return value, RedactionReport("opaque"), value
    clean, report = redact_payload(value, RedactionPolicy(max_string_chars=10**9), return_report=True)
    encoded = canonical_json_bytes(clean)
    return encoded, report, clean


class LLMCaptureManager:
    """Capture provider metadata and optional redacted body without wire replay."""

    def __init__(
        self,
        *,
        policy: LLMCapturePolicy | None = None,
        archive: ArtifactArchive | None = None,
        runtime_store: Any | None = None,
    ) -> None:
        self.policy = policy or LLMCapturePolicy()
        self.archive = archive
        self.runtime_store = runtime_store

    @staticmethod
    def _size(value: Any) -> int:
        try:
            return len(canonical_json_bytes(value))
        except (TypeError, ValueError):
            return len(str(value).encode("utf-8"))

    def capture(
        self,
        *,
        request_id: str,
        session_id: str,
        run_id: str | None = None,
        invocation_id: str | None = None,
        attempt: int = 1,
        attempt_id: str | None = None,
        provider: str,
        model: str,
        request: Any,
        response: Any,
        usage: Mapping[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> LLMCaptureResult:
        if not request_id.strip() or not session_id.strip():
            raise ValueError("request_id and session_id are required")
        if attempt < 1:
            raise ValueError("attempt must be positive")
        mode = self.policy.mode
        request_size = self._size(request)
        response_size = self._size(response)
        metadata: dict[str, Any] = {
            "capture_mode": mode,
            "capture_policy_version": self.policy.redaction_policy.version,
            "provider": provider,
            "model": model,
            "request_id": request_id,
            "invocation_id": invocation_id,
            "attempt": attempt,
            "attempt_id": attempt_id,
            "request_shape_hash": request_shape_hash(request),
            "request_size_bytes": request_size,
            "response_size_bytes": response_size,
            "usage": dict(usage or {}),
            "latency_ms": latency_ms,
            "body_present": False,
        }
        llm_ref = f"llm:{request_id}:attempt:{attempt_id or attempt}"
        if mode == "off":
            status = "off"
        elif mode == "metadata-only":
            status = "metadata-only"
        else:
            status = "saved"
            try:
                request_bytes, request_report, clean_request = _body(request)
                response_bytes, response_report, clean_response = _body(response)
                metadata["redaction"] = {
                    "request": request_report.to_dict(),
                    "response": response_report.to_dict(),
                }
                for name, payload, clean, mime in (
                    ("request", request_bytes, clean_request, "application/json"),
                    ("response", response_bytes, clean_response, "application/json"),
                ):
                    if self.archive is not None and self.policy.archive_bodies:
                        ref = self.archive.archive(
                            payload,
                            mime_type=mime,
                            encoding="binary",
                            scope="llm-capture",
                            redaction_policy=RedactionPolicy(
                                version=self.policy.redaction_policy.version,
                                max_inline_bytes=self.policy.max_body_bytes,
                                max_string_chars=10**9,
                            ),
                            metadata={"request_id": request_id, "body": name, "attempt": attempt},
                        )
                        metadata[f"{name}_ref"] = ref.to_dict()
                        metadata[f"{name}_sha256"] = ref.sha256
                        metadata[f"{name}_size_bytes"] = ref.size_bytes
                    else:
                        bounded = payload[: self.policy.max_body_bytes]
                        metadata[f"{name}_body"] = bounded.decode("utf-8", errors="replace")
                        metadata[f"{name}_sha256"] = "sha256:" + hashlib.sha256(payload).hexdigest()
                        metadata[f"{name}_size_bytes"] = len(payload)
                        metadata[f"{name}_truncated"] = len(payload) > len(bounded)
                    del clean
                metadata["body_present"] = True
            except (ArtifactArchiveError, OSError, TypeError, ValueError) as error:
                return LLMCaptureResult("failed", None, metadata, str(error))

        if self.runtime_store is not None and hasattr(self.runtime_store, "write_llm_capture"):
            try:
                self.runtime_store.write_llm_capture(
                    llm_ref,
                    session_id=session_id,
                    run_id=run_id,
                    request_id=request_id,
                    model=model,
                    capture_status=status,
                    metadata=metadata,
                )
            except Exception as error:
                return LLMCaptureResult("failed", None, metadata, str(error))
        return LLMCaptureResult(status, llm_ref, metadata)


__all__ = [
    "CaptureMode",
    "LLMCaptureError",
    "LLMCaptureManager",
    "LLMCapturePolicy",
    "LLMCaptureResult",
    "request_shape_hash",
]
