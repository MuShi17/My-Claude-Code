"""C02 可控工具夹具（承接 GAP-I02-06）。

提供四种**具备真实挂起/取消语义**的形态，供 C02 的测试矩阵与后续 Change 复用：
- 可暂停：工具真正挂起在线程事件上，直到测试显式放行（不是只置标志位）；
- 长输出：产生大体积输出（用于有界预览/分页/协议帧边界）；
- 可取消 shell：持有真实子进程句柄，可被真实终止（用于取消语义）；
- 晚到结果：在独立线程中延迟返回，不阻塞事件循环。

夹具只依赖标准库；调用方传 tmp_path，不写入用户真实目录。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "PausableTool",
    "LongOutputTool",
    "CancellableShellTool",
    "LateResultTool",
    "FixtureToolbox",
]


@dataclass
class PausableTool:
    """可暂停工具：真正挂起，直到 ``release()`` 被调用。

    ``wait_started(timeout)`` 让测试确认工具已进入挂起状态，避免用 sleep 猜测。
    """

    name: str = "pausable"
    calls: list[dict[str, Any]] = field(default_factory=list)
    _gate: threading.Event = field(default_factory=threading.Event, repr=False)
    _entered: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def started(self) -> bool:
        return self._entered.is_set()

    @property
    def released(self) -> bool:
        return self._gate.is_set()

    def __call__(self, inp: dict[str, Any] | None = None) -> str:
        self.calls.append(dict(inp or {}))
        self._entered.set()
        self._gate.wait(timeout=30)
        return "resumed" if self._gate.is_set() else "timed-out"

    def wait_started(self, timeout: float = 5.0) -> bool:
        return self._entered.wait(timeout=timeout)

    def release(self) -> None:
        self._gate.set()


@dataclass
class LongOutputTool:
    """长输出工具：生成指定行数/体积的确定性输出。"""

    lines: int = 5000
    prefix: str = "line"
    name: str = "long_output"

    def __call__(self, inp: dict[str, Any] | None = None) -> str:
        count = int((inp or {}).get("lines", self.lines))
        return "\n".join(f"{self.prefix}-{i:06d}-{'x' * 40}" for i in range(count))


@dataclass
class CancellableShellTool:
    """可取消 shell：持有真实子进程句柄，``cancel()`` 真正终止进程。"""

    name: str = "cancellable_shell"
    seconds: int = 30
    _process: subprocess.Popen | None = field(default=None, repr=False)

    def command(self) -> list[str]:
        """返回可被终止的跨平台命令（Python 子进程，避免 shell 差异）。"""

        return [sys.executable, "-c", f"import time;time.sleep({self.seconds})"]

    def start(self, cwd: Path | None = None) -> subprocess.Popen:
        self._process = subprocess.Popen(self.command(), cwd=str(cwd) if cwd else None)
        return self._process

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def cancel(self, timeout: float = 5.0) -> int | None:
        """终止子进程并返回退出码；未启动时返回 None。"""

        if self._process is None:
            return None
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
                self._process.kill()
                self._process.wait(timeout=timeout)
        return self._process.returncode


@dataclass
class LateResultTool:
    """晚到结果：在独立线程中延迟返回，不阻塞调用方的事件循环。"""

    delay_seconds: float = 0.05
    name: str = "late_result"
    returned: bool = False
    _thread: threading.Thread | None = field(default=None, repr=False)

    def start_async(self) -> None:
        def _run() -> None:
            time.sleep(max(0.0, self.delay_seconds))
            self.returned = True

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def __call__(self, inp: dict[str, Any] | None = None) -> str:
        return "late-result"


@dataclass
class FixtureToolbox:
    """把四种夹具聚合为单一入口，便于测试按需取用。"""

    pausable: PausableTool = field(default_factory=PausableTool)
    long_output: LongOutputTool = field(default_factory=LongOutputTool)
    cancellable_shell: CancellableShellTool = field(default_factory=CancellableShellTool)
    late_result: LateResultTool = field(default_factory=LateResultTool)

    def as_handlers(self) -> dict[str, Callable[[dict[str, Any]], str]]:
        return {
            self.pausable.name: self.pausable,
            self.long_output.name: self.long_output,
            self.late_result.name: self.late_result,
        }
