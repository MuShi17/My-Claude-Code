"""C02 可控 Provider 注入夹具（承接 items/02 的 C02 义务，GAP-C02-10）。

提供四种可控形态，供 C02/C03 的测试矩阵复用：
- 可暂停：流在指定 chunk 后挂起，直到显式放行；
- 抛错：在指定位置抛出 Provider 错误（覆盖重试/失败路径）；
- 长输出：产生大体积 chunk 序列（覆盖有界预览/分页/帧边界）；
- 晚到结果：最后一个 chunk 延迟到达（覆盖过期/竞态）。

夹具不联网、不导入 Provider SDK；与 `runtime_fixtures.FakeProviderScript` 互补
（后者是确定性场景脚本，本文件提供**时序可控**的注入点）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = [
    "ControllableProvider",
    "ProviderFixtureToolbox",
]


@dataclass
class ControllableProvider:
    """时序可控的假 Provider：chunk 序列 + 挂起/抛错/延迟注入点。"""

    chunks: list[dict[str, Any]] = field(default_factory=list)
    provider: str = "anthropic"
    fail_at: int | None = None
    fail_with: str = "provider failure"
    pause_at: int | None = None
    late_at: int | None = None
    late_seconds: float = 0.05

    _gate: threading.Event = field(default_factory=threading.Event, repr=False)
    _entered: threading.Event = field(default_factory=threading.Event, repr=False)
    emitted: int = 0
    failed: bool = False

    def __post_init__(self) -> None:
        if not self.chunks:
            self.chunks = [
                {"kind": "text", "text": "hello "},
                {"kind": "text", "text": "world"},
                {"kind": "final", "text": "done"},
            ]

    # ─── 控制面 ────────────────────────────────────────────

    @property
    def paused(self) -> bool:
        return self._entered.is_set() and not self._gate.is_set()

    def wait_paused(self, timeout: float = 5.0) -> bool:
        """等待流进入挂起状态（避免用 sleep 猜测）。"""

        return self._entered.wait(timeout=timeout)

    def release(self) -> None:
        self._gate.set()

    # ─── 流接口 ────────────────────────────────────────────

    def stream(self) -> Iterator[dict[str, Any]]:
        for index, chunk in enumerate(self.chunks):
            if self.fail_at is not None and index == self.fail_at:
                self.failed = True
                raise RuntimeError(self.fail_with)
            if self.pause_at is not None and index == self.pause_at:
                self._entered.set()
                self._gate.wait(timeout=30)
            if self.late_at is not None and index == self.late_at:
                threading.Event().wait(max(0.0, self.late_seconds))
            self.emitted += 1
            yield dict(chunk)

    def final_response(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "chunks": list(self.chunks),
            "usage": {"input_tokens": 7, "output_tokens": 3},
            "finish_reason": "stop",
        }

    # ─── 预置形态 ──────────────────────────────────────────

    @classmethod
    def long_output(cls, chunks: int = 500, size: int = 64) -> "ControllableProvider":
        payload = [{"kind": "text", "text": "x" * size} for _ in range(chunks)]
        payload.append({"kind": "final", "text": "done"})
        return cls(chunks=payload)

    @classmethod
    def failing(cls, at: int = 1) -> "ControllableProvider":
        return cls(fail_at=at)

    @classmethod
    def pausing(cls, at: int = 1) -> "ControllableProvider":
        return cls(pause_at=at)

    @classmethod
    def late(cls, at: int = 2, seconds: float = 0.05) -> "ControllableProvider":
        return cls(late_at=at, late_seconds=seconds)


@dataclass
class ProviderFixtureToolbox:
    """聚合入口：一次取到四种形态。"""

    @staticmethod
    def long_output(chunks: int = 500) -> ControllableProvider:
        return ControllableProvider.long_output(chunks=chunks)

    @staticmethod
    def failing(at: int = 1) -> ControllableProvider:
        return ControllableProvider.failing(at=at)

    @staticmethod
    def pausing(at: int = 1) -> ControllableProvider:
        return ControllableProvider.pausing(at=at)

    @staticmethod
    def late(at: int = 2, seconds: float = 0.05) -> ControllableProvider:
        return ControllableProvider.late(at=at, seconds=seconds)
