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
    total_tool_duration_ms = ask.get("total_tool_duration_ms", 0)
    tool_success_rate = ask.get("tool_success_rate", 1.0)
    denied_tools = ask.get("denied_tools", 0)

    # 从 turn 行提取
    first_token_ms = 0
    total_cache_read = 0
    compactions = 0
    per_turn_cache_rates: list[float] = []
    for t in turns:
        if t.get("first_token_ms", 0) > 0:
            if first_token_ms == 0:
                first_token_ms = t["first_token_ms"]
        total_cache_read += t.get("cache_read_tokens", 0)
        if t.get("compaction_triggered"):
            compactions += 1
        # 每轮缓存命中率（cap 在 1.0）
        turn_input = t.get("input_tokens", 1) or 1
        per_turn_cache_rates.append(min(t.get("cache_read_tokens", 0) / turn_input, 1.0))

    # 缓存命中率 = 各轮平均值
    cache_hit_rate = sum(per_turn_cache_rates) / len(per_turn_cache_rates) if per_turn_cache_rates else 0.0

    return {
        "turns": total_turns,
        "duration_ms": total_duration_ms,
        "first_token_ms": first_token_ms,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "tool_calls": total_tool_calls,
        "tool_success_rate": round(tool_success_rate, 4),
        "total_tool_duration_ms": total_tool_duration_ms,
        "denied_tools": denied_tools,
        "compactions": compactions,
        "cache_hit_rate": round(cache_hit_rate, 4),
    }


def _empty_trace_result() -> dict[str, Any]:
    return {
        "turns": 0, "duration_ms": 0, "first_token_ms": 0,
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0,
        "tool_calls": 0, "tool_success_rate": 1.0, "total_tool_duration_ms": 0,
        "denied_tools": 0, "compactions": 0, "cache_hit_rate": 0.0,
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
    first_tokens = [t["first_token_ms"] for t in tasks if t["first_token_ms"] > 0]
    avg_first_token = sum(first_tokens) // len(first_tokens) if first_tokens else 0
    cache_hit_rate = sum(t["cache_hit_rate"] for t in tasks) / total if total > 0 else 0.0

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
        del data["total_duration_ms"]

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


def aggregate_reports(run_id: str, reports: list[dict[str, Any]]) -> dict[str, Any]:
    """将多次运行的多份 report 聚合为带 mean/stddev 的综合报告。"""
    if not reports:
        return {}

    import statistics

    task_ids = [t["id"] for t in reports[0]["tasks"]]
    num_runs = len(reports)

    # ── 聚合每个 task ──────────────────────────────────────
    agg_tasks: list[dict] = []
    for tid in task_ids:
        # 收集该 task 在所有 run 中的指标
        task_runs = []
        for r in reports:
            match = [t for t in r["tasks"] if t["id"] == tid]
            if match:
                task_runs.append(match[0])

        pass_count = sum(1 for tr in task_runs if tr["passed"])
        numeric_fields = [
            "turns", "duration_ms", "first_token_ms",
            "input_tokens", "output_tokens", "cache_read_tokens",
            "tool_calls", "tool_success_rate", "total_tool_duration_ms",
            "denied_tools", "compactions", "cache_hit_rate",
        ]

        agg_task: dict = {
            "id": tid,
            "category": task_runs[0]["category"] if task_runs else "unknown",
            "pass_rate": round(pass_count / len(task_runs), 4) if task_runs else 0,
            "num_runs": len(task_runs),
        }

        for field in numeric_fields:
            values = [tr.get(field, 0) for tr in task_runs]
            if len(values) >= 2:
                agg_task[f"{field}_mean"] = round(statistics.mean(values), 1)
                agg_task[f"{field}_std"] = round(statistics.stdev(values), 1)
            elif len(values) == 1:
                agg_task[f"{field}_mean"] = round(values[0], 1)
                agg_task[f"{field}_std"] = 0.0
            else:
                agg_task[f"{field}_mean"] = 0
                agg_task[f"{field}_std"] = 0.0

        agg_tasks.append(agg_task)

    # ── 聚合 summary ───────────────────────────────────────
    total = len(agg_tasks)
    avg_pass_rate = sum(t["pass_rate"] for t in agg_tasks) / total if total > 0 else 0
    total_duration_mean = sum(t["duration_ms_mean"] for t in agg_tasks)
    total_input_mean = sum(t["input_tokens_mean"] for t in agg_tasks)
    total_output_mean = sum(t["output_tokens_mean"] for t in agg_tasks)
    first_tokens = [t["first_token_ms_mean"] for t in agg_tasks if t["first_token_ms_mean"] > 0]
    avg_first_token = sum(first_tokens) // len(first_tokens) if first_tokens else 0
    cache_rates = [t["cache_hit_rate_mean"] for t in agg_tasks]
    avg_cache = sum(cache_rates) / len(cache_rates) if cache_rates else 0

    # By category
    by_category: dict[str, dict] = {}
    for t in agg_tasks:
        cat = t["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed_rate": 0, "avg_duration_ms_mean": 0, "task_count": 0}
        by_category[cat]["total"] += 1
        by_category[cat]["passed_rate"] += t["pass_rate"]
        by_category[cat]["avg_duration_ms_mean"] += t["duration_ms_mean"]
        by_category[cat]["task_count"] += 1
    for cat, data in by_category.items():
        n = data["task_count"]
        data["passed_rate"] = round(data["passed_rate"] / n, 4) if n > 0 else 0
        data["avg_duration_ms_mean"] = round(data["avg_duration_ms_mean"] / n, 1) if n > 0 else 0
        del data["task_count"]

    return {
        "run_id": run_id,
        "num_runs": num_runs,
        "aggregate": {
            "summary": {
                "total_tasks": total,
                "avg_pass_rate": round(avg_pass_rate, 4),
                "total_duration_ms_mean": round(total_duration_mean, 1),
                "total_input_tokens_mean": round(total_input_mean, 1),
                "total_output_tokens_mean": round(total_output_mean, 1),
                "avg_first_token_ms": round(avg_first_token, 1),
                "avg_cache_hit_rate": round(avg_cache, 4),
            },
            "by_category": by_category,
            "tasks": agg_tasks,
        },
        "runs": reports,
    }

