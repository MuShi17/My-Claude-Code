"""Local, content-addressed storage for bounded runtime payloads.

Artifacts are auxiliary data.  A caller must obtain a successful
``ArtifactRef`` before it emits a reference into a canonical event.  The
archive therefore commits the bytes first, then its metadata, and optionally
mirrors the metadata into the runtime store.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .redaction import RedactionPolicy, RedactionReport, redact_payload
from .runtime_event import canonical_json_bytes

ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_REF_PREFIX = "artifact:sha256:"


class ArtifactArchiveError(RuntimeError):
    code = "artifact_archive_error"


class ArtifactNotFoundError(ArtifactArchiveError):
    code = "artifact_not_found"


class ArtifactIntegrityError(ArtifactArchiveError):
    code = "artifact_integrity_error"


class ArtifactAccessError(ArtifactArchiveError):
    code = "artifact_access_denied"


class ArtifactSizeLimitError(ArtifactArchiveError):
    code = "artifact_size_limit"


class ArtifactMetadataError(ArtifactArchiveError):
    code = "artifact_metadata_error"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Stable reference and integrity metadata for one archived payload."""

    ref: str
    sha256: str
    size_bytes: int
    mime_type: str
    encoding: str
    scope: str
    redaction_version: str
    created_at: str
    metadata_version: int = ARTIFACT_SCHEMA_VERSION

    @property
    def digest(self) -> str:
        return self.sha256.removeprefix("sha256:")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "encoding": self.encoding,
            "scope": self.scope,
            "redaction_version": self.redaction_version,
            "created_at": self.created_at,
            "metadata_version": self.metadata_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRef":
        try:
            item = cls(
                ref=str(value["ref"]),
                sha256=str(value["sha256"]),
                size_bytes=int(value["size_bytes"]),
                mime_type=str(value["mime_type"]),
                encoding=str(value.get("encoding", "binary")),
                scope=str(value.get("scope", "runtime")),
                redaction_version=str(value.get("redaction_version", "unknown")),
                created_at=str(value.get("created_at", "")),
                metadata_version=int(value.get("metadata_version", ARTIFACT_SCHEMA_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ArtifactMetadataError(f"invalid artifact metadata: {error}") from error
        _validate_ref(item)
        return item

    def placeholder(self, *, preview: str | None = None, truncated: bool = True) -> dict[str, Any]:
        """Return a bounded, canonical-event-safe representation."""

        value: dict[str, Any] = {
            "kind": "bounded_ref",
            "ref": self.ref,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "encoding": self.encoding,
            "scope": self.scope,
            "redaction_version": self.redaction_version,
            "metadata_version": self.metadata_version,
            "truncated": truncated,
        }
        if preview:
            value["preview"] = preview
        return value


@dataclass(frozen=True, slots=True)
class ArtifactDiagnostic:
    code: str
    ref: str | None
    message: str
    path: str | None = None
    repairable: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": self.message, "repairable": self.repairable}
        if self.ref is not None:
            result["ref"] = self.ref
        if self.path is not None:
            result["path"] = self.path
        return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_ref(ref: ArtifactRef) -> None:
    if not ref.ref.startswith(ARTIFACT_REF_PREFIX):
        raise ArtifactMetadataError(f"unsupported artifact ref {ref.ref!r}")
    expected = ARTIFACT_REF_PREFIX + ref.digest
    if ref.ref != expected or len(ref.digest) != 64:
        raise ArtifactMetadataError(f"artifact ref is not content addressed: {ref.ref!r}")
    try:
        int(ref.digest, 16)
    except ValueError as error:
        raise ArtifactMetadataError(f"artifact digest is not hexadecimal: {ref.digest!r}") from error
    if ref.size_bytes < 0:
        raise ArtifactMetadataError("artifact size must not be negative")


class ArtifactArchive:
    """Atomic local archive with optional runtime-store metadata mirroring."""

    def __init__(
        self,
        root: str | Path,
        *,
        metadata_store: Any | None = None,
        max_read_bytes: int = 64 * 1024,
        fault_hook: Any | None = None,
    ) -> None:
        self.root = Path(root)
        self.metadata_store = metadata_store
        self.max_read_bytes = max_read_bytes
        self.fault_hook = fault_hook

    def _fault(self, *points: str) -> None:
        if self.fault_hook is None:
            return
        checker = getattr(self.fault_hook, "check", None)
        for point in points:
            if checker is not None:
                checker(point)
            elif callable(self.fault_hook):
                self.fault_hook(point)

    @staticmethod
    def _redacted_bytes(
        value: bytes | str | Any,
        *,
        encoding: str,
        policy: RedactionPolicy,
    ) -> tuple[bytes, RedactionReport]:
        if isinstance(value, bytes):
            # Bytes are treated as opaque; a caller that needs secret removal
            # must decode them before entering the archive.
            return value, RedactionReport(policy.version)
        if isinstance(value, str):
            # Archive the complete redacted text.  The normal inline bound is
            # deliberately not used here because it is the archive's job to
            # hold the bounded-by-reference payload.
            text_policy = RedactionPolicy(
                version=policy.version,
                max_inline_bytes=policy.max_inline_bytes,
                max_string_chars=max(len(value) + 1, policy.max_string_chars),
                placeholder=policy.placeholder,
                sensitive_keys=policy.sensitive_keys,
            )
            # Apply the value-level scrub line by line so one credential does
            # not erase an otherwise useful multi-line tool result.
            pieces: list[str] = []
            redacted_paths: list[str] = []
            bounded_paths: list[str] = []
            for index, line in enumerate(value.splitlines(keepends=True)):
                clean, report = redact_payload(line, text_policy, return_report=True)
                pieces.append(str(clean))
                redacted_paths.extend(f"line[{index}].{path}" for path in report.redacted_paths)
                bounded_paths.extend(f"line[{index}].{path}" for path in report.bounded_paths)
            report = RedactionReport(
                policy.version,
                tuple(redacted_paths),
                tuple(bounded_paths),
            )
            codec = "utf-8" if encoding == "binary" else encoding
            return "".join(pieces).encode(codec), report
        clean, report = redact_payload(value, policy, return_report=True)
        return canonical_json_bytes(clean), report

    def _paths(self, digest: str) -> tuple[Path, Path]:
        directory = self.root / "sha256" / digest[:2]
        return directory / f"{digest}.bin", directory / f"{digest}.json"

    @staticmethod
    def _atomic_write(path: Path, data: bytes, *, archive: "ArtifactArchive", label: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
        temporary = path.with_name(temp_name)
        try:
            archive._fault(f"artifact.{label}", f"archive.{label}")
            with open(temporary, "xb") as handle:
                handle.write(data)
                handle.flush()
                archive._fault("artifact.fsync", "archive.fsync")
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _metadata(self, ref: ArtifactRef, extra: Mapping[str, Any] | None) -> dict[str, Any]:
        value = ref.to_dict()
        value["artifact_schema_version"] = ARTIFACT_SCHEMA_VERSION
        if extra:
            value["metadata"] = json.loads(json.dumps(dict(extra), ensure_ascii=False, default=str))
        return value

    def archive(
        self,
        value: bytes | str | Any,
        *,
        mime_type: str = "application/octet-stream",
        encoding: str = "binary",
        scope: str = "runtime",
        redaction_policy: RedactionPolicy | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        policy = redaction_policy or RedactionPolicy()
        payload, report = self._redacted_bytes(value, encoding=encoding, policy=policy)
        digest = hashlib.sha256(payload).hexdigest()
        ref = ArtifactRef(
            ref=ARTIFACT_REF_PREFIX + digest,
            sha256="sha256:" + digest,
            size_bytes=len(payload),
            mime_type=mime_type,
            encoding=encoding,
            scope=scope,
            redaction_version=report.version,
            created_at=_utc_now(),
        )
        _validate_ref(ref)
        content_path, metadata_path = self._paths(digest)

        # A complete existing object is reusable.  A half-written object is
        # diagnosed instead of silently overwriting a possible corruption.
        if content_path.exists() or metadata_path.exists():
            if not content_path.exists() or not metadata_path.exists():
                raise ArtifactIntegrityError(f"incomplete artifact object {ref.ref}")
            existing = self._read_metadata_file(metadata_path)
            if any(
                getattr(existing, name) != getattr(ref, name)
                for name in ("ref", "sha256", "size_bytes", "mime_type", "encoding", "scope", "redaction_version")
            ):
                raise ArtifactIntegrityError(f"metadata mismatch for {ref.ref}")
            self._verify_content(existing, content_path)
            if self.metadata_store is not None and hasattr(self.metadata_store, "read_artifact_metadata"):
                try:
                    mirrored = self.metadata_store.read_artifact_metadata(existing.ref)
                    if mirrored is None:
                        self.metadata_store.write_artifact_metadata(
                            existing.ref,
                            sha256=existing.sha256,
                            size_bytes=existing.size_bytes,
                            mime_type=existing.mime_type,
                            encoding=existing.encoding,
                            scope=existing.scope,
                            redaction_version=existing.redaction_version,
                            metadata=self._metadata(existing, metadata),
                        )
                except Exception as error:
                    raise ArtifactMetadataError(f"runtime artifact metadata commit failed: {error}") from error
            return existing

        self._atomic_write(content_path, payload, archive=self, label="write")
        full_metadata = self._metadata(
            ref,
            {
                **dict(metadata or {}),
                "redaction_report": report.to_dict(),
            },
        )
        metadata_bytes = json.dumps(
            full_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self._atomic_write(metadata_path, metadata_bytes, archive=self, label="metadata")
        if self.metadata_store is not None and hasattr(self.metadata_store, "write_artifact_metadata"):
            try:
                self.metadata_store.write_artifact_metadata(
                    ref.ref,
                    sha256=ref.sha256,
                    size_bytes=ref.size_bytes,
                    mime_type=ref.mime_type,
                    encoding=ref.encoding,
                    scope=ref.scope,
                    redaction_version=ref.redaction_version,
                    metadata=full_metadata,
                )
            except Exception as error:
                raise ArtifactMetadataError(f"runtime artifact metadata commit failed: {error}") from error
        return ref

    archive_payload = archive

    def _read_metadata_file(self, path: Path) -> ArtifactRef:
        try:
            return ArtifactRef.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(str(path)) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ArtifactArchiveError) as error:
            raise ArtifactIntegrityError(f"invalid artifact metadata {path}: {error}") from error

    def _verify_content(self, ref: ArtifactRef, path: Path) -> bytes:
        try:
            payload = path.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(ref.ref) from error
        except OSError as error:
            raise ArtifactArchiveError(f"cannot read {ref.ref}: {error}") from error
        if len(payload) != ref.size_bytes or hashlib.sha256(payload).hexdigest() != ref.digest:
            raise ArtifactIntegrityError(f"hash/size mismatch for {ref.ref}")
        return payload

    def _resolve(self, ref: ArtifactRef | Mapping[str, Any] | str) -> tuple[ArtifactRef, Path]:
        if isinstance(ref, ArtifactRef):
            item = ref
        elif isinstance(ref, Mapping):
            item = ArtifactRef.from_dict(ref)
        else:
            text = str(ref)
            if text.startswith(ARTIFACT_REF_PREFIX):
                digest = text.removeprefix(ARTIFACT_REF_PREFIX)
                item = ArtifactRef(
                    ref=text,
                    sha256="sha256:" + digest,
                    size_bytes=0,
                    mime_type="application/octet-stream",
                    encoding="binary",
                    scope="runtime",
                    redaction_version="unknown",
                    created_at="",
                )
                _validate_ref(item)
            else:
                raise ArtifactMetadataError(f"invalid artifact ref {text!r}")
        _validate_ref(item)
        content_path, metadata_path = self._paths(item.digest)
        if metadata_path.exists():
            stored = self._read_metadata_file(metadata_path)
            if stored.ref != item.ref:
                raise ArtifactIntegrityError(f"reference metadata mismatch for {item.ref}")
            item = stored
        return item, content_path

    def read(
        self,
        ref: ArtifactRef | Mapping[str, Any] | str,
        *,
        max_bytes: int | None = None,
        preview: bool = False,
        allowed_scopes: Iterable[str] | None = None,
    ) -> bytes | str:
        item, path = self._resolve(ref)
        if allowed_scopes is not None and item.scope not in set(allowed_scopes):
            raise ArtifactAccessError(f"scope {item.scope!r} is not allowed")
        payload = self._verify_content(item, path)
        limit = self.max_read_bytes if max_bytes is None else max_bytes
        if limit < 1:
            raise ArtifactSizeLimitError("max_bytes must be positive")
        if len(payload) > limit and not preview:
            raise ArtifactSizeLimitError(f"artifact is {len(payload)} bytes, limit is {limit}")
        if preview:
            payload = payload[:limit]
        if item.encoding != "binary" or item.mime_type.startswith("text/"):
            return payload.decode(item.encoding if item.encoding != "binary" else "utf-8", errors="replace")
        return payload

    read_bounded = read

    def inspect(self, ref: ArtifactRef | Mapping[str, Any] | str) -> ArtifactRef:
        item, path = self._resolve(ref)
        self._verify_content(item, path)
        return item

    def diagnose(self, ref: str | None = None) -> list[ArtifactDiagnostic]:
        if ref is not None:
            try:
                self.inspect(ref)
            except ArtifactArchiveError as error:
                return [ArtifactDiagnostic(error.code, ref, str(error), repairable=False)]
            return []
        diagnostics: list[ArtifactDiagnostic] = []
        content_paths = set(self.root.glob("sha256/*/*.bin")) if self.root.exists() else set()
        metadata_paths = set(self.root.glob("sha256/*/*.json")) if self.root.exists() else set()
        for content_path in sorted(content_paths | metadata_paths):
            digest = content_path.stem
            content, metadata = self._paths(digest)
            if content not in content_paths or metadata not in metadata_paths:
                diagnostics.append(ArtifactDiagnostic(
                    "orphan_archive" if content in content_paths else "dangling_ref",
                    ARTIFACT_REF_PREFIX + digest,
                    "artifact content and metadata are not both present",
                    str(content_path),
                    repairable=content in content_paths,
                ))
                continue
            try:
                item = self._read_metadata_file(metadata)
                self._verify_content(item, content)
            except ArtifactArchiveError as error:
                diagnostics.append(ArtifactDiagnostic(error.code, ARTIFACT_REF_PREFIX + digest, str(error), str(content_path)))
        return diagnostics

    def repair_orphans(self, *, adopt: bool = False) -> list[ArtifactDiagnostic]:
        """Diagnose orphan files; optionally adopt valid content with metadata.

        No file is deleted.  Adoption is explicit so normal startup never
        performs cleanup or changes canonical history.
        """

        diagnostics = self.diagnose()
        if not adopt:
            return diagnostics
        for item in diagnostics:
            if item.code != "orphan_archive" or not item.path:
                continue
            path = Path(item.path)
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest != path.stem:
                continue
            ref = ArtifactRef(
                ref=ARTIFACT_REF_PREFIX + digest,
                sha256="sha256:" + digest,
                size_bytes=len(payload),
                mime_type="application/octet-stream",
                encoding="binary",
                scope="repaired",
                redaction_version="unknown",
                created_at=_utc_now(),
            )
            self._atomic_write(
                self._paths(digest)[1],
                json.dumps(ref.to_dict(), sort_keys=True, separators=(",", ":")).encode(),
                archive=self,
                label="metadata",
            )
        return self.diagnose()


__all__ = [
    "ARTIFACT_REF_PREFIX",
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactAccessError",
    "ArtifactArchive",
    "ArtifactArchiveError",
    "ArtifactDiagnostic",
    "ArtifactIntegrityError",
    "ArtifactMetadataError",
    "ArtifactNotFoundError",
    "ArtifactRef",
    "ArtifactSizeLimitError",
]
