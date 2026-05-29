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
