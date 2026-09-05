"""Compatibility entry point for the C11 shadow-parity harness."""

from __future__ import annotations

from typing import Any, Iterable

from .cutover import (
    GapRegister,
    ParityMismatch,
    ParityReport,
    StableSemanticComparator,
    stable_semantic_projection,
)


ShadowParityReport = ParityReport


class ShadowParityHarness(StableSemanticComparator):
    """Named facade used by offline integration fixtures and embedders."""

    def run(
        self,
        canonical: Any,
        legacy: Any,
        *,
        scenario: str = "unnamed",
        evidence: Iterable[str] = (),
    ) -> ShadowParityReport:
        return self.compare(canonical, legacy, scenario=scenario, evidence=evidence)


__all__ = [
    "GapRegister",
    "ParityMismatch",
    "ShadowParityHarness",
    "ShadowParityReport",
    "stable_semantic_projection",
]
