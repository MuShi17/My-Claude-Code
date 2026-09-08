"""Shared Provider capacity failure used by projection and dispatch gates."""

from __future__ import annotations


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
    ) -> None:
        self.provider = provider
        self.request_size_bytes = (
            int(request_size_bytes) if request_size_bytes is not None else None
        )
        self.request_budget_bytes = (
            int(request_budget_bytes) if request_budget_bytes is not None else None
        )
        self.ref = ref
        if self.request_size_bytes is not None and self.request_budget_bytes is not None:
            message = (
                f"{provider or 'Provider'} request exceeds local context capacity: "
                f"{self.request_size_bytes} > {self.request_budget_bytes} bytes"
            )
        else:
            message = "archived tool result cannot fit the current Provider capacity"
        super().__init__(message)


__all__ = ["ProviderCapacityError"]
