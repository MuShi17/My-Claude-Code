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


def _run_single_pass(tasks: list[dict[str, Any]], run_id: str, run_label: str, verbose: bool) -> dict[str, Any]:
    """执行一次完整的 benchmark（所有 tasks 串行一遍）。"""
    run_dir = RUNS_DIR / run_id / run_label
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    task_results = []
    for i, task in enumerate(tasks):
        task_id = task["id"]
        if verbose:
            print(f"  [{i+1}/{len(tasks)}] {task_id} ... ", end="", flush=True)

        workspace = Path(tempfile.mkdtemp(prefix=f"bench_{task_id}_"))
        fixture_src = PROJECT_ROOT / task.get("fixture_repo", "")
        if fixture_src.exists():
            shutil.copytree(fixture_src, workspace, dirs_exist_ok=True)

        result = run_task(task, workspace, run_id)
        task_results.append(result)

        if verbose:
            status = "PASS" if result["passed"] else "FAIL"
            if result.get("error"):
                status += f" ({result['error']})"
            print(status)

        latest_traces = _find_latest_session_traces_dir()
        if latest_traces:
            for tf in sorted(latest_traces.glob("*.jsonl")):
                shutil.copy2(tf, traces_dir / f"{task_id}.jsonl")
                break

        shutil.rmtree(workspace, ignore_errors=True)

    return build_report(run_id, task_results, traces_dir)


def run_all(num_runs: int = 1, verbose: bool = True) -> dict[str, Any]:
    """执行所有 benchmark task，可选多次运行取平均。"""
    tasks = load_tasks()
    run_id = time.strftime("%Y%m%dT%H%M%S")

    reports = []
    for r in range(num_runs):
        label = f"run{r+1:02d}"
        if verbose and num_runs > 1:
            print(f"\n--- Run {r+1}/{num_runs} ---")
        report = _run_single_pass(tasks, run_id, label, verbose)
        reports.append(report)

    if num_runs == 1:
        final_report = reports[0]
        save_report(final_report, RUNS_DIR / run_id / "run01" / "report.json")
    else:
        from .reporter import aggregate_reports
        final_report = aggregate_reports(run_id, reports)
        agg_path = RUNS_DIR / run_id / "aggregate_report.json"
        save_report(final_report, agg_path)
        if verbose:
            avg = final_report["aggregate"]
            passed_rate = avg["summary"]["avg_pass_rate"]
            print(f"\nAggregate ({num_runs} runs): pass_rate={passed_rate:.1%}, "
                  f"duration_mean={avg['summary']['total_duration_ms_mean']:.0f}ms, "
                  f"first_token={avg['summary']['avg_first_token_ms']:.0f}ms")

    return final_report


if __name__ == "__main__":
    num_runs = 1
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--runs":
        num_runs = int(args[1])
    report = run_all(num_runs=num_runs)
    if "aggregate" in report:
        summary = report["aggregate"]["summary"]
        print(f"\nResult: pass_rate={summary['avg_pass_rate']:.1%}, "
              f"duration={summary['total_duration_ms_mean']:.0f}ms, "
              f"first_token={summary['avg_first_token_ms']:.0f}ms")
    else:
        s = report["summary"]
        print(f"\nResult: {s['passed']}/{s['total']} passed ({s['pass_rate']:.1%})")
    sys.exit(0)
