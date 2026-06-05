# Benchmark Runner 设计文档

> **For Claude:** 实现时使用 superpowers:writing-plans 生成实现计划。

**Goal:** 建立 benchmark 评估体系，对 Mini Claude Code 进行自动化回归测试和性能基准追踪。

**Architecture:** `benchmark/` 独立模块 — `runner.py` 负责任务编排执行，`reporter.py` 负责从 trace 提取指标生成结构化报告。

**Tech Stack:** Python 3.11+, subprocess, json, 无第三方依赖

---

## 1. 设计目标

| 维度 | 决策 |
|------|------|
| 目的 | 回归测试（pass/fail）+ 性能基准（全指标追踪） |
| Task 类型 | 文本编辑（保留现有）+ 简单编程任务（新增） |
| Runner 架构 | benchmark/ 独立模块 + 结构化 JSON 报告 |
| Agent 调用 | subprocess CLI `mini-claude-py --yolo` |
| 报告输出 | `benchmark_runs/{timestamp}/report.json` |

---

## 2. 模块结构

```
benchmark/
  coding_tasks.json   # task 定义（JSON）
  runner.py            # 主入口：按序执行 task，收集结果
  reporter.py          # 报告生成：读 trace → 聚合 → report.json

benchmark_runs/
  20260529T220000/
    report.json        # 聚合报告
    traces/
      readme_intro_locked.jsonl   # 每个 task 的 trace 副本
      create_python_module.jsonl
      ...
```

---

## 3. Task 定义格式

`benchmark/coding_tasks.json`:

```json
{
  "schema_version": 2,
  "description": "Benchmark tasks for Mini Claude Code regression testing",
  "tasks": [
    {
      "id": "unique_id",
      "prompt": "agent 提示词",
      "fixture_repo": "test/fixtures/xxx",
      "allowed_tools": ["read_file", "edit_file"],
      "step_budget": 4,
      "verifier": "python -c \"assert condition\"",
      "category": "text-edit",
      "expected_artifact": "人类可读的描述"
    }
  ]
}
```

字段说明：
- **id**: 唯一标识符
- **prompt**: 发送给 agent 的用户消息
- **fixture_repo**: `test/fixtures/` 下的子目录，作为工作区初始状态
- **allowed_tools**: task 允许使用的工具白名单（预留，runner 不强制限制）
- **step_budget**: 最大 turns 数（传递给 `--max-turns`）
- **verifier**: shell 命令，退出码 0 表示通过
- **category**: 分组维度（documentation / text-edit / tool-boundary / coding）
- **expected_artifact**: 人类可读的期望结果描述

---

## 4. Runner 流程

```
for each task in coding_tasks.json:
  1. 复制 fixture 到临时目录
  2. cd 工作区 && mini-claude-py --yolo --max-turns {step_budget} "{prompt}"
  3. 在工作区运行 task.verifier → pass/fail
  4. 从 ~/.mini-claude/sessions/{id}/traces/ 复制 trace 到 benchmark_runs/
  5. 清理临时工作区
```

关键决策：
- **subprocess 调用**: 真正的进程隔离，测试用户实际使用的 CLI 路径
- **--yolo 模式**: 跳过所有权限确认，实现完全自动化
- **串行执行**: 避免 API rate limit 干扰指标准确性
- **失败继续**: 单个 task 失败不中断，跑完全部获取完整通过率
- **单 task 超时**: 默认 5 分钟，可配置

---

## 5. Reporter 输出

`benchmark_runs/{timestamp}/report.json`:

```json
{
  "run_id": "20260529T220000",
  "summary": {
    "total": 12,
    "passed": 10,
    "failed": 2,
    "pass_rate": 0.83,
    "total_duration_ms": 324000,
    "total_input_tokens": 45000,
    "total_output_tokens": 8200,
    "avg_first_token_ms": 320,
    "cache_hit_rate": 0.45
  },
  "by_category": {
    "documentation": {"total": 2, "passed": 2, "avg_duration_ms": 12000},
    "...": {}
  },
  "tasks": [
    {
      "id": "readme_intro_locked",
      "category": "documentation",
      "passed": true,
      "verifier_output": "",
      "duration_ms": 8500,
      "turns": 2,
      "first_token_ms": 280,
      "input_tokens": 2100,
      "output_tokens": 340,
      "cache_read_tokens": 1200,
      "tool_calls": 2,
      "compactions": 0,
      "trace_path": "benchmark_runs/20260529T220000/traces/readme_intro_locked.jsonl"
    }
  ]
}
```

指标来源 trace 字段：
- `first_token_ms` → `turn.first_token_ms`
- `turns` → `ask.total_turns`
- `input_tokens` / `output_tokens` → 累加所有 turn
- `cache_read_tokens` → `turn.cache_read_tokens`
- `tool_calls` → `ask.total_tool_calls`
- `compactions` → `turn.compaction_triggered` 计数

---

## 6. Task 列表

### 保留的现有任务（7 个，verifier 已修复）

| # | id | category | 描述 |
|---|-----|----------|------|
| 1 | readme_intro_locked | documentation | 替换 README 开头句子 |
| 2 | readme_schema_note | documentation | 修改 README 列表第一项 |
| 3 | sample_beta_locked | text-edit | sample.txt 中替换 beta |
| 4 | sample_gamma_locked | text-edit | sample.txt 中替换 gamma |
| 5 | invalid_patch_recovery | tool-boundary | 无效 patch 后恢复并完成修改 |
| 6 | path_escape_recovery | tool-boundary | 拒绝路径逃逸后完成修改 |
| 7 | repeated_read_recovery | tool-boundary | 拒绝重复读取后完成修改 |

### 新增的编程任务（5 个）

| # | id | category | 描述 |
|---|-----|----------|------|
| 8 | create_python_module | coding | 创建 calculator.py 实现加减乘除 |
| 9 | fix_syntax_error | coding | 修复 Python 文件中的语法错误 |
| 10 | add_unit_test | coding | 为已有函数编写 pytest 测试 |
| 11 | refactor_function | coding | 拆分过长函数为更小的子函数 |
| 12 | search_and_replace | coding | 跨文件搜索模式并批量替换 |

---

## 7. 实现顺序

```
Task 1: 修复 coding_tasks.json — 清理 pico 依赖 task，新增 5 个 coding task
Task 2: 新增 test/fixtures 目录 — 为新 coding task 创建 fixture
Task 3: 实现 reporter.py — 从 trace 提取指标，生成 report.json
Task 4: 实现 runner.py — task 执行编排、subprocess 调用、结果收集
Task 5: 端到端验证 — 选择 2-3 个 task 试跑，确认全链路通
```
