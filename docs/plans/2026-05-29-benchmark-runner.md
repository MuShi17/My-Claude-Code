# Benchmark Runner 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 benchmark 自动化评估系统 — runner.py 编排执行 + reporter.py 生成性能报告。

**Architecture:** `benchmark/` 独立模块，subprocess 调用 `mini-claude-py --yolo`，串行执行 task，从 trace JSONL 提取指标生成 `benchmark_runs/{timestamp}/report.json`。

**Tech Stack:** Python 3.11+, subprocess, json, tempfile, shutil, 无第三方依赖

**Design Doc:** `docs/plans/2026-05-29-benchmark-runner-design.md`

---

### Task 1: 重构 coding_tasks.json + 新增 fixture

**Files:**
- Modify: `benchmark/coding_tasks.json`
- Create: `test/fixtures/empty_repo/.gitkeep`
- Create: `test/fixtures/broken_module/broken.py`
- Create: `test/fixtures/long_function/long_func.py`
- Create: `test/fixtures/multi_file/a.txt`
- Create: `test/fixtures/multi_file/b.txt`
- Create: `test/fixtures/multi_file/c.txt`
- Create: `test/fixtures/math_utils/math_utils.py`

**Step 1: 重写 coding_tasks.json**

将 `benchmark/coding_tasks.json` 替换为以下内容 — 保留 7 个原有 task（修复 verifier 从 `python3` 改为 `python`，移除 pico 路径引用）+ 新增 5 个 coding task：

```json
{
  "schema_version": 2,
  "description": "Benchmark tasks for Mini Claude Code regression testing and performance tracking.",
  "tasks": [
    {
      "id": "readme_intro_locked",
      "prompt": "Read README.md and replace the placeholder opening sentence with 'This fixture is a locked benchmark workspace.'",
      "fixture_repo": "test/fixtures/bench_repo_readme",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 4,
      "verifier": "python -c \"from pathlib import Path; text = Path('README.md').read_text(encoding='utf-8'); assert 'This fixture is a locked benchmark workspace.' in text\"",
      "category": "documentation",
      "expected_artifact": "README.md opening sentence is locked benchmark workspace text"
    },
    {
      "id": "readme_schema_note",
      "prompt": "Read README.md and replace the first bullet item with a sentence that says the benchmark schema and baseline are fixed.",
      "fixture_repo": "test/fixtures/bench_repo_readme",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 4,
      "verifier": "python -c \"from pathlib import Path; text = Path('README.md').read_text(encoding='utf-8'); assert 'schema and baseline are fixed' in text\"",
      "category": "documentation",
      "expected_artifact": "README.md contains the schema and baseline note"
    },
    {
      "id": "sample_beta_locked",
      "prompt": "Read sample.txt and replace 'beta' with 'beta-locked'.",
      "fixture_repo": "test/fixtures/bench_repo_patch",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 4,
      "verifier": "python -c \"from pathlib import Path; text = Path('sample.txt').read_text(encoding='utf-8'); assert 'beta-locked' in text\"",
      "category": "text-edit",
      "expected_artifact": "sample.txt contains beta-locked"
    },
    {
      "id": "sample_gamma_locked",
      "prompt": "Read sample.txt and replace 'gamma' with 'gamma-locked'.",
      "fixture_repo": "test/fixtures/bench_repo_patch",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 4,
      "verifier": "python -c \"from pathlib import Path; text = Path('sample.txt').read_text(encoding='utf-8'); assert 'gamma-locked' in text\"",
      "category": "text-edit",
      "expected_artifact": "sample.txt contains gamma-locked"
    },
    {
      "id": "invalid_patch_recovery",
      "prompt": "Read README.md. Recover after invalid patch arguments and finish the README patch by adding a line 'recovered after invalid patch args' before the Notes section.",
      "fixture_repo": "test/fixtures/bench_repo_readme",
      "allowed_tools": ["edit_file", "read_file"],
      "step_budget": 5,
      "verifier": "python -c \"from pathlib import Path; text = Path('README.md').read_text(encoding='utf-8'); assert 'recovered after invalid patch args' in text\"",
      "category": "tool-boundary",
      "expected_artifact": "README.md reflects recovery after invalid patch args"
    },
    {
      "id": "path_escape_recovery",
      "prompt": "Read sample.txt. Reject a path escape attempt and still finish the sample.txt patch by changing 'alpha' to 'alpha-guarded'.",
      "fixture_repo": "test/fixtures/bench_repo_patch",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 5,
      "verifier": "python -c \"from pathlib import Path; text = Path('sample.txt').read_text(encoding='utf-8'); assert 'alpha-guarded' in text\"",
      "category": "tool-boundary",
      "expected_artifact": "sample.txt reflects recovery after path escape rejection"
    },
    {
      "id": "repeated_read_recovery",
      "prompt": "Read sample.txt. Recover after repeated identical reads and still finish the patch by changing 'placeholder' to 'repeat-guarded'.",
      "fixture_repo": "test/fixtures/bench_repo_patch",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 6,
      "verifier": "python -c \"from pathlib import Path; text = Path('sample.txt').read_text(encoding='utf-8'); assert 'repeat-guarded' in text\"",
      "category": "tool-boundary",
      "expected_artifact": "sample.txt reflects recovery after repeated identical read rejection"
    },
    {
      "id": "create_python_module",
      "prompt": "Create a new file calculator.py that defines four functions: add(a,b) returning a+b, subtract(a,b) returning a-b, multiply(a,b) returning a*b, divide(a,b) returning a/b. Make divide raise ValueError when b is 0.",
      "fixture_repo": "test/fixtures/empty_repo",
      "allowed_tools": ["write_file"],
      "step_budget": 6,
      "verifier": "python -c \"from calculator import add, subtract, multiply, divide; assert add(2,3)==5; assert subtract(10,4)==6; assert multiply(3,5)==15; assert divide(8,2)==4.0; import traceback; ok=False; try: divide(1,0); except ValueError: ok=True; assert ok, 'should raise ValueError'\"",
      "category": "coding",
      "expected_artifact": "calculator.py with working add/subtract/multiply/divide functions"
    },
    {
      "id": "fix_syntax_error",
      "prompt": "Read broken.py and fix all the syntax errors so the module can be imported successfully.",
      "fixture_repo": "test/fixtures/broken_module",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 5,
      "verifier": "python -c \"import broken; assert hasattr(broken, 'greet'); assert broken.greet('World') == 'Hello, World!'\"",
      "category": "coding",
      "expected_artifact": "broken.py is syntactically valid and the greet function works"
    },
    {
      "id": "add_unit_test",
      "prompt": "Read math_utils.py which contains a factorial function and is_prime function. Create a test_math_utils.py file using pytest that tests both functions with at least 3 test cases each (including edge cases).",
      "fixture_repo": "test/fixtures/math_utils",
      "allowed_tools": ["read_file", "write_file"],
      "step_budget": 6,
      "verifier": "python -m pytest test_math_utils.py -v --tb=short",
      "category": "coding",
      "expected_artifact": "test_math_utils.py with at least 6 pytest test cases that all pass"
    },
    {
      "id": "refactor_function",
      "prompt": "Read long_func.py which contains a process_data function that is too long. Refactor it by extracting two helper functions: validate_items and format_report. The behavior must stay identical — running the script should produce exactly the same output.",
      "fixture_repo": "test/fixtures/long_function",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 6,
      "verifier": "python -c \"import long_func; import io,sys; out=io.StringIO(); sys.stdout=out; long_func.process_data(['a','b','']); sys.stdout=sys.__stdout__; assert 'Valid: 2' in out.getvalue(); assert 'Invalid: 1' in out.getvalue(); assert 'Total: 3' in out.getvalue()\"",
      "category": "coding",
      "expected_artifact": "long_func.py has validate_items and format_report helper functions, behavior unchanged"
    },
    {
      "id": "search_and_replace",
      "prompt": "There are three files a.txt, b.txt, c.txt each containing the word 'TODO'. Search across all three files and replace every occurrence of 'TODO' with 'DONE'. Do not touch any other content.",
      "fixture_repo": "test/fixtures/multi_file",
      "allowed_tools": ["grep_search", "read_file", "edit_file"],
      "step_budget": 8,
      "verifier": "python -c \"from pathlib import Path; for f in ['a.txt','b.txt','c.txt']: t=Path(f).read_text(encoding='utf-8'); assert 'DONE' in t; assert 'TODO' not in t\"",
      "category": "coding",
      "expected_artifact": "All three txt files have TODO replaced with DONE"
    }
  ]
}
```

**Step 2: 创建 empty_repo fixture**

```bash
mkdir -p test/fixtures/empty_repo && touch test/fixtures/empty_repo/.gitkeep
```

**Step 3: 创建 broken_module fixture**

`test/fixtures/broken_module/broken.py`:

```python
def greet(name)
    return f"Hello, {name}!":


if __name__ == "__main__":
    print(greet("World"))
```

**Step 4: 创建 math_utils fixture**

`test/fixtures/math_utils/math_utils.py`:

```python
def factorial(n):
    """Return the factorial of n."""
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def is_prime(n):
    """Return True if n is prime."""
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True
```

**Step 5: 创建 long_function fixture**

`test/fixtures/long_function/long_func.py`:

```python
def process_data(items):
    results = []
    for item in items:
        if item and len(item.strip()) > 0:
            results.append(item.strip().upper())
    valid = len(results)
    invalid = len(items) - valid
    print(f"Valid: {valid}")
    print(f"Invalid: {invalid}")
    print(f"Total: {len(items)}")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r}")


if __name__ == "__main__":
    process_data(['a', 'b', ''])
```

**Step 6: 创建 multi_file fixture**

`test/fixtures/multi_file/a.txt`、`b.txt`、`c.txt` — 各包含一行 `TODO item X`:

```bash
echo "TODO item A" > test/fixtures/multi_file/a.txt
echo "TODO item B" > test/fixtures/multi_file/b.txt
echo "TODO item C" > test/fixtures/multi_file/c.txt
```

**Step 7: 验证 fixture 完整性**

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch
ls test/fixtures/empty_repo/.gitkeep test/fixtures/broken_module/broken.py test/fixtures/math_utils/math_utils.py test/fixtures/long_function/long_func.py test/fixtures/multi_file/a.txt test/fixtures/multi_file/b.txt test/fixtures/multi_file/c.txt
```

**Step 8: Commit**

```bash
git add benchmark/coding_tasks.json test/fixtures/
git commit -m "refactor: update benchmark tasks — fix verifiers, add 5 coding tasks with fixtures"
```

---

### Task 2: 实现 reporter.py

**Files:**
- Create: `benchmark/reporter.py`

**Step 1: 编写 reporter.py**

```python
"""Reporter — 从 trace JSONL 提取指标，生成结构化 report.json。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def load_trace_data(trace_path: Path) -> dict[str, Any]:
    """读取 JSONL trace 文件，返回 ask 行和 turn 列表的合并指标。"""
    if not trace_path.exists():
        return _empty_trace_result()

    lines = [line for line in trace_path.read_text(encoding="utf-8").strip().split("\n") if line]
    if not lines:
        return _empty_trace_result()

    ask = None
    turns = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") == "ask":
            ask = entry
        elif entry.get("type") == "turn":
            turns.append(entry)

    if not ask:
        return _empty_trace_result()

    # 从 ask 行提取
    total_turns = ask.get("total_turns", len(turns))
    total_input = ask.get("total_input_tokens", 0)
    total_output = ask.get("total_output_tokens", 0)
    total_tool_calls = ask.get("total_tool_calls", 0)
    total_duration_ms = ask.get("total_duration_ms", 0)

    # 从 turn 行提取
    first_token_ms = 0
    total_cache_read = 0
    total_cache_create = 0
    compactions = 0
    for t in turns:
        if t.get("first_token_ms", 0) > 0:
            if first_token_ms == 0:
                first_token_ms = t["first_token_ms"]
        total_cache_read += t.get("cache_read_tokens", 0)
        total_cache_create += t.get("cache_create_tokens", 0)
        if t.get("compaction_triggered"):
            compactions += 1

    # 缓存命中率
    cache_total = total_input if total_input > 0 else 1
    cache_hit_rate = total_cache_read / cache_total

    return {
        "turns": total_turns,
        "duration_ms": total_duration_ms,
        "first_token_ms": first_token_ms,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "cache_create_tokens": total_cache_create,
        "tool_calls": total_tool_calls,
        "compactions": compactions,
        "cache_hit_rate": round(cache_hit_rate, 4),
    }


def _empty_trace_result() -> dict[str, Any]:
    return {
        "turns": 0, "duration_ms": 0, "first_token_ms": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_create_tokens": 0,
        "tool_calls": 0, "compactions": 0, "cache_hit_rate": 0.0,
    }


def build_report(
    run_id: str,
    task_results: list[dict[str, Any]],
    traces_dir: Path,
) -> dict[str, Any]:
    """根据 task 执行结果和 trace 文件生成最终 report。"""

    tasks = []
    for tr in task_results:
        task_id = tr["id"]
        trace_path = traces_dir / f"{task_id}.jsonl"

        trace_data = load_trace_data(trace_path)

        tasks.append({
            "id": task_id,
            "category": tr.get("category", "unknown"),
            "passed": tr["passed"],
            "verifier_output": tr.get("verifier_output", ""),
            "error": tr.get("error"),
            "trace_path": str(trace_path),
            **trace_data,
        })

    # Summary
    total = len(tasks)
    passed = sum(1 for t in tasks if t["passed"])
    failed = total - passed
    total_duration_ms = sum(t["duration_ms"] for t in tasks)
    total_input = sum(t["input_tokens"] for t in tasks)
    total_output = sum(t["output_tokens"] for t in tasks)
    total_cache_read = sum(t["cache_read_tokens"] for t in tasks)
    # 首Token 只统计有值的
    first_tokens = [t["first_token_ms"] for t in tasks if t["first_token_ms"] > 0]
    avg_first_token = sum(first_tokens) // len(first_tokens) if first_tokens else 0
    cache_total = sum(t["input_tokens"] for t in tasks)
    cache_hit_rate = (total_cache_read / cache_total) if cache_total > 0 else 0.0

    # By category
    by_category: dict[str, dict] = {}
    for t in tasks:
        cat = t["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0, "avg_duration_ms": 0, "total_duration_ms": 0}
        by_category[cat]["total"] += 1
        if t["passed"]:
            by_category[cat]["passed"] += 1
        by_category[cat]["total_duration_ms"] += t["duration_ms"]

    for cat, data in by_category.items():
        data["avg_duration_ms"] = data["total_duration_ms"] // data["total"] if data["total"] > 0 else 0
        del data["total_duration_ms"]  # 只保留 avg

    return {
        "run_id": run_id,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
            "total_duration_ms": total_duration_ms,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "avg_first_token_ms": avg_first_token,
            "cache_hit_rate": round(cache_hit_rate, 4),
        },
        "by_category": by_category,
        "tasks": tasks,
    }


def save_report(report: dict[str, Any], report_path: Path) -> None:
    """将 report 写入 JSON 文件。"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
```

**Step 2: 验证 reporter.py 基本功能**

使用模拟 trace 数据测试 reporter：

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import json, tempfile, os
from pathlib import Path
from benchmark.reporter import load_trace_data, build_report, save_report

# 模拟 trace 数据
td = Path(tempfile.mkdtemp())
trace_file = td / 'test_task.jsonl'
trace_file.write_text(json.dumps({'type':'ask','ask_index':1,'message':'test','total_turns':2,'total_duration_ms':5000,'total_input_tokens':200,'total_output_tokens':100,'total_tool_calls':1,'timestamp':'2026-05-29T12:00:00Z'}, ensure_ascii=False) + '\n' + json.dumps({'type':'turn','turn_index':1,'first_token_ms':300,'input_tokens':100,'output_tokens':50,'cache_read_tokens':80,'cache_create_tokens':0,'finish_reason':'tool_calls','tool_calls':[{'name':'read_file','input':{},'duration_ms':50,'result_length':100}],'total_duration_ms':2500}, ensure_ascii=False) + '\n' + json.dumps({'type':'turn','turn_index':2,'first_token_ms':0,'input_tokens':100,'output_tokens':50,'cache_read_tokens':0,'cache_create_tokens':0,'finish_reason':'stop','tool_calls':[],'total_duration_ms':2500}, ensure_ascii=False))

# 测试 load_trace_data
td2 = load_trace_data(trace_file)
assert td2['turns'] == 2
assert td2['first_token_ms'] == 300
assert td2['cache_read_tokens'] == 80
assert td2['input_tokens'] == 200
assert td2['tool_calls'] == 1
assert td2['compactions'] == 0
assert td2['cache_hit_rate'] == 0.4

# 测试 build_report + save_report
task_results = [{'id': 'test_task', 'category': 'text-edit', 'passed': True, 'verifier_output': ''}]
report = build_report('test_run', task_results, td)
assert report['summary']['total'] == 1
assert report['summary']['pass_rate'] == 1.0

report_path = td / 'report.json'
save_report(report, report_path)
assert report_path.exists()
saved = json.loads(report_path.read_text(encoding='utf-8'))
assert saved['summary']['passed'] == 1

import shutil; shutil.rmtree(td)
print('PASS')
"
```

**Step 3: Commit**

```bash
git add benchmark/reporter.py
git commit -m "feat: add benchmark reporter — trace-to-report aggregation"
```

---

### Task 3: 实现 runner.py

**Files:**
- Create: `benchmark/runner.py`
- Modify: `src/mini_claude/__main__.py` (need to check entry point)

**Step 1: 确认 CLI 入口**

先检查当前 `__main__.py` 的 CLI 入口方式，确认 `mini-claude-py` 命令的实际调用方式：

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && grep -n "def main\|argparse\|parse_args" src/mini_claude/__main__.py | head -20
```

如果 `mini-claude-py` 命令因 Windows 锁而无法使用，则改为 `python -m mini_claude` 作为 runner 的调用方式。

**Step 2: 编写 runner.py**

```python
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
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
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
                text=True,
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
    """构建 agent 调用命令。优先使用 python -m mini_claude。"""
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

    # 按修改时间排序，找最新的有 traces 的会话
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
            # 找到该 task 对应的 trace 文件
            for tf in sorted(latest_traces.glob("*.jsonl")):
                dest = traces_dir / f"{task_id}.jsonl"
                shutil.copy2(tf, dest)
                break  # 只取第一个（即最新的）

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
```

**Step 3: 验证 CLI 入口可用性**

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
from benchmark.runner import load_tasks, run_all
tasks = load_tasks()
assert len(tasks) == 12
print(f'Loaded {len(tasks)} tasks')
for t in tasks:
    print(f'  {t[\"id\"]} [{t[\"category\"]}] budget={t[\"step_budget\"]}')
"
```

**Step 4: Commit**

```bash
git add benchmark/runner.py
git commit -m "feat: add benchmark runner — task orchestration and subprocess execution"
```

---

### Task 4: 端到端验证

**Files:**
- Modify: `benchmark/.gitignore` (创建)

**Step 1: 创建 benchmark_runs 的 .gitignore**

```bash
echo "*" > benchmark_runs/.gitignore && mkdir -p benchmark_runs
echo "benchmark_runs/" >> .gitignore
```

**Step 2: 完整模块导入验证**

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import io, sys
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()

# 验证所有模块导入
from benchmark.runner import load_tasks, run_all, _build_agent_cmd, _find_latest_session_traces_dir
from benchmark.reporter import load_trace_data, build_report, save_report

# 验证 task 加载
tasks = load_tasks()
assert len(tasks) == 12

# 验证 category 分布
cats = {}
for t in tasks:
    cats[t['category']] = cats.get(t['category'], 0) + 1
assert cats == {'documentation': 2, 'text-edit': 2, 'tool-boundary': 3, 'coding': 5}

# 验证所有 fixtures 存在
from pathlib import Path
root = Path('.')
for t in tasks:
    fixture = root / t['fixture_repo']
    assert fixture.exists(), f'Missing fixture: {t[\"fixture_repo\"]}'

# 验证 CLI 命令构建
cmd = _build_agent_cmd('test prompt', 4)
assert '--yolo' in cmd
assert '--max-turns' in cmd
assert '4' in cmd

sys.stdout = sys.__stdout__
sys.stderr = sys.__stderr__
print('ALL PASS')
"
```

**Step 3: 运行 1 个简单 task 做快速冒烟测试（可选，需要 API key）**

如果能确认 API key 可用：

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -m benchmark.runner
```

如果 API key 不可用，跳过此步，标记为需手动验证。

**Step 4: Commit**

```bash
git add benchmark/ benchmark_runs/.gitignore .gitignore
git commit -m "test: add end-to-end integration verification for benchmark system"
```

---

### 实现顺序汇总

```
Task 1: coding_tasks.json + fixtures    ← 数据基础
Task 2: reporter.py                     ← 报告生成
Task 3: runner.py                       ← 核心执行
Task 4: 端到端验证                       ← 最终验证
```
