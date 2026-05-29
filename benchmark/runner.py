"""Benchmark Runner — 按序执行 task，收集结果。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .reporter import build_report, save_report

BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parent
TASKS_FILE = BENCHMARK_DIR / "coding_tasks.json"
RUNS_DIR = PROJECT_ROOT / "benchmark_runs"
DEFAULT_TIMEOUT = 300  # 每个 task 默认 5 分钟超时


def _load_env() -> dict[str, str]:
    """从项目根 .env 文件加载环境变量，不覆盖已有系统环境变量。"""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return {}
    env = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            env[key] = value
    return env


# 模块加载时预加载 .env 中的 API key
_env_cache = _load_env()


def load_tasks() -> list[dict[str, Any]]:
    """从 coding_tasks.json 加载 task 列表。"""
    with open(TASKS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tasks", [])


def run_task(task: dict[str, Any], workspace: Path, run_id: str) -> dict[str, Any]:
    """执行单个 benchmark task。"""
    task_id = task["id"]
    prompt = task["prompt"]
    step_budget = task.get("step_budget", 4)
    timeout = task.get("timeout", DEFAULT_TIMEOUT)

    result: dict[str, Any] = {
        "id": task_id,
        "category": task.get("category", "unknown"),
        "passed": False,
        "verifier_output": "",
        "error": None,
    }

    # ── 1. 运行 agent ─────────────────────────────────────
    agent_cmd = _build_agent_cmd(prompt, step_budget)
    try:
        proc = subprocess.run(
            agent_cmd,
            cwd=str(workspace),
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            env={**os.environ, **_env_cache, "PYTHONPATH": str(PROJECT_ROOT / "src"), "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        result["error"] = f"Task timed out after {timeout}s"
        return result
    except Exception as e:
        result["error"] = f"Agent process failed: {e}"
        return result

    # ── 2. 运行 verifier ──────────────────────────────────
    verifier = task.get("verifier", "")
    if verifier:
        try:
            vproc = subprocess.run(
                verifier,
                cwd=str(workspace),
                shell=True,
                capture_output=True,
                encoding="utf-8",
                timeout=60,
            )
            result["passed"] = vproc.returncode == 0
            result["verifier_output"] = vproc.stderr.strip() or vproc.stdout.strip()
        except subprocess.TimeoutExpired:
            result["passed"] = False
            result["verifier_output"] = "Verifier timed out"
        except Exception as e:
            result["passed"] = False
            result["verifier_output"] = str(e)
    else:
        # 没有 verifier 时默认通过
        result["passed"] = True

    return result


def _build_agent_cmd(prompt: str, step_budget: int) -> list[str]:
    """构建 agent 调用命令。"""
    return [
        sys.executable, "-m", "mini_claude",
        "--yolo",
        "--max-turns", str(step_budget),
        prompt,
    ]


def _find_latest_session_traces_dir() -> Path | None:
    """查找最近一次会话的 traces 目录。"""
    sessions_dir = Path.home() / ".mini-claude" / "sessions"
    if not sessions_dir.exists():
        return None

    dirs = sorted(
        [d for d in sessions_dir.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for d in dirs:
        traces = d / "traces"
        if traces.exists():
            return traces
    return None


def run_all(verbose: bool = True) -> dict[str, Any]:
    """执行所有 benchmark task，返回最终 report。"""
    tasks = load_tasks()
    run_id = time.strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_DIR / run_id
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    task_results = []
    for i, task in enumerate(tasks):
        task_id = task["id"]
        if verbose:
            print(f"[{i+1}/{len(tasks)}] {task_id} ... ", end="", flush=True)

        # 创建隔离工作区
        fixture_src = PROJECT_ROOT / task.get("fixture_repo", "")
        workspace = Path(tempfile.mkdtemp(prefix=f"bench_{task_id}_"))
        if fixture_src.exists():
            shutil.copytree(fixture_src, workspace, dirs_exist_ok=True)

        # 执行
        result = run_task(task, workspace, run_id)
        task_results.append(result)

        if verbose:
            status = "PASS" if result["passed"] else "FAIL"
            if result.get("error"):
                status += f" ({result['error']})"
            print(status)

        # 复制 trace
        latest_traces = _find_latest_session_traces_dir()
        if latest_traces:
            for tf in sorted(latest_traces.glob("*.jsonl")):
                dest = traces_dir / f"{task_id}.jsonl"
                shutil.copy2(tf, dest)
                break

        # 清理
        shutil.rmtree(workspace, ignore_errors=True)

    # 生成报告
    report = build_report(run_id, task_results, traces_dir)
    report_path = run_dir / "report.json"
    save_report(report, report_path)

    return report


if __name__ == "__main__":
    report = run_all()
    passed = report["summary"]["passed"]
    total = report["summary"]["total"]
    print(f"\nResult: {passed}/{total} passed ({report['summary']['pass_rate']:.1%})")
    sys.exit(0 if passed == total else 1)
