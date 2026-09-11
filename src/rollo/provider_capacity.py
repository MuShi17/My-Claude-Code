"""Shared Provider capacity failure used by projection and dispatch gates."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class ProviderCapacityError(RuntimeError):
    """A bounded failure raised when a Provider request cannot be sent."""

    code = "provider_capacity_exhausted"

    def __init__(
        self,
        *,
        provider: str | None = None,
        request_size_bytes: int | None = None,
        request_budget_bytes: int | None = None,
        ref: str | None = None,
        diagnostics: Sequence[Any] | None = None,
        cycle_identity: str | None = None,
    ) -> None:
        self.provider = provider
        self.request_size_bytes = (
            int(request_size_bytes) if request_size_bytes is not None else None
        )
        self.request_budget_bytes = (
            int(request_budget_bytes) if request_budget_bytes is not None else None
        )
        self.ref = ref
        self.diagnostics = tuple(diagnostics or ())[:32]
        self.cycle_identity = str(cycle_identity) if cycle_identity else None
        if self.request_size_bytes is not None and self.request_budget_bytes is not None:
            message = (
                f"{provider or 'Provider'} request exceeds local context capacity: "
                f"{self.request_size_bytes} > {self.request_budget_bytes} bytes"
            )
        else:
            message = "archived tool result cannot fit the current Provider capacity"
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        def diagnostic_value(item: Any) -> dict[str, Any]:
            if hasattr(item, "to_dict"):
                value = item.to_dict()
                return value if isinstance(value, dict) else {"code": self.code}
            if isinstance(item, dict):
                return {
                    "code": str(item.get("code", self.code))[:96],
                    "message": "provider capacity diagnostic",
                    "severity": str(item.get("severity", "error"))[:16],
                }
            return {"code": self.code, "message": "provider capacity diagnostic"}

        return {
            "code": self.code,
            "provider": self.provider,
            "request_size_bytes": self.request_size_bytes,
            "request_budget_bytes": self.request_budget_bytes,
            "ref": self.ref,
            "cycle_identity": self.cycle_identity,
            "diagnostics": [diagnostic_value(item) for item in self.diagnostics],
        }


__all__ = ["ProviderCapacityError"]
