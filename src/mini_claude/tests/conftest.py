"""测试隔离强制点（C01 / GAP-I02-01 闭合）。

本文件是本仓库唯一的全局测试隔离入口，目标是让任何测试都不会读到或写入
用户的真实 ``~/.mini-claude``、``~/.claude`` 数据，也不会因为导入期常量
（``session.SESSION_DIR``）而绕过环境变量隔离。

隔离设计：
- 每个测试自动获得临时 HOME 与临时 ``MINI_CLAUDE_RUNTIME_DIR``；
- 显式 monkeypatch 导入期常量（``session.SESSION_DIR``），因为在模块导入后
  修改环境变量不会重定向已经固化的常量；
- 不加载仓库根 ``.env``，避免真实密钥进入测试进程。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 必须在任何 mini_claude 模块导入之前生效：mini_claude.__main__ 在导入期无条件
# 调用 load_dotenv()，而 python-dotenv 1.2+ 会在那里读取本开关。fixture 内设置
# 已经太晚，因此这里强制赋值（不用 setdefault：外部若给 0/false 会静默关闭隔离）。
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import mini_claude.session as session_module  # noqa: E402

_REAL_ENV_KEYS = ("HOME", "USERPROFILE", "MINI_CLAUDE_RUNTIME_DIR")
# 覆盖仓库根 .env 会注入的全部键，避免只断言两个 API key 而漏掉其它真实配置。
_REAL_KEY_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "MINI_CLAUDE_MODEL",
    "MINI_CLAUDE_THINKING_EFFORT",
)


def _restore(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...], saved: dict[str, str | None]) -> None:
    for name in names:
        value = saved.get(name)
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def isolated_runtime_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """为每个测试提供临时 HOME / runtime 数据目录，并重定向导入期常量。"""

    saved = {name: os.environ.get(name) for name in _REAL_ENV_KEYS}
    saved_keys = {name: os.environ.get(name) for name in _REAL_KEY_KEYS}

    root = tmp_path_factory.mktemp("isolated-runtime")
    home = root / "home"
    data_dir = root / "mini-claude"
    home.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MINI_CLAUDE_RUNTIME_DIR", str(data_dir))
    # 不把真实密钥带进测试进程；需要密钥的测试自行 setenv。
    for name in _REAL_KEY_KEYS:
        monkeypatch.delenv(name, raising=False)
    # python-dotenv 1.2+ 上游开关：阻止仓库根 .env 向测试进程注入真实密钥。
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    # 导入期常量：环境变量改动不会重定向它，必须显式替换。
    monkeypatch.setattr(session_module, "SESSION_DIR", data_dir / "sessions", raising=False)

    yield {"home": home, "data_dir": data_dir}

    _restore(monkeypatch, _REAL_ENV_KEYS, saved)
    _restore(monkeypatch, _REAL_KEY_KEYS, saved_keys)


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """一个空的临时 workspace 根，供 ProjectContext 相关测试使用。"""

    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root
