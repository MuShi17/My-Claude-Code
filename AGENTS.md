# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在此仓库中工作时提供指导。

## 项目概述

Mini Claude Code — 一个精简的编程智能体，复现了 Claude Code 的核心架构。

**当前工作范围**：`python/mini_claude/`（Python）。

## 常用命令

```bash
# 安装（需要 Python 3.11+）
cd src && pip install -e .

# 运行
mini-claude-py                           # 交互式 REPL 模式
mini-claude-py "提示词"                   # one-shot 模式
python -m mini_claude "提示词"            # 备选入口

# 权限模式
mini-claude-py --yolo "提示词"            # 跳过所有确认
mini-claude-py --plan "提示词"            # 只读计划模式
mini-claude-py --accept-edits "提示词"    # 自动批准文件编辑
mini-claude-py --dont-ask "提示词"        # 自动拒绝确认（适用于 CI）

# 其他参数
mini-claude-py --resume                  # 恢复上次会话
mini-claude-py --thinking-effort max     # 设置模型思考强度（默认 max）
mini-claude-py --no-thinking             # 关闭模型思考
mini-claude-py --model <名称>            # 覆盖模型（环境变量：MINI_CLAUDE_MODEL）
mini-claude-py --api-base <URL>          # OpenAI 兼容 API 端点
mini-claude-py --max-cost 0.50           # 费用上限（美元）
mini-claude-py --max-turns 20            # 轮次上限

# TypeScript 版（独立使用）
npm install && npm run build
npm start [-- --yolo --plan ...]
```

API 配置：设置 `ANTHROPIC_API_KEY`（Anthropic 格式，推荐）或 `OPENAI_API_KEY` + `OPENAI_BASE_URL`（OpenAI 兼容格式）。两者均支持自定义 base URL。思考强度可通过 `MINI_CLAUDE_THINKING_EFFORT` 配置，默认值为 `max`，可选 `none`、`low`、`high`、`max`。

## Terminal-Bench 2.1 / Harbor

在 Windows PowerShell 中从仓库根目录运行。必须使用已安装 Harbor 的 Python 3.12+ 环境，并将仓库根目录加入 `PYTHONPATH`，否则 Harbor 无法导入自定义 Adapter `benchmark.harbor_agent:MiniClaudeHarborAgent`。

```powershell
conda activate py313
$env:PYTHONPATH = (Get-Location).Path
$runDir = "benchmark_runs/$(Get-Date -Format 'yyyy-MM-dd__HH-mm-ss')"

& "$env:CONDA_PREFIX\Scripts\harbor.exe" run `
  --agent benchmark.harbor_agent:MiniClaudeHarborAgent `
  --model deepseek-v4-flash `
  --dataset terminal-bench/terminal-bench-2-1 `
  --task terminal-bench/vulnerable-secret `
  --jobs-dir $runDir `
  --n-concurrent 1 `
  --env-file .env `
  --yes
```

运行前确认 `.env` 已填写 `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`MINI_CLAUDE_MODEL` 和 `MINI_CLAUDE_THINKING_EFFORT`。DeepSeek Anthropic 配置示例：`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`、`MINI_CLAUDE_MODEL=deepseek-v4-flash`、`MINI_CLAUDE_THINKING_EFFORT=max`。

`--dataset` 使用完整数据集标识，`--task` 使用完整任务标识；`--n-concurrent 1` 便于单任务调试和查看日志。结果保存在 `benchmark_runs/<时间戳>/`，其中 `result.json` 包含 Harbor 结果和 usage 信息。

### 预构建 Mini Claude 运行时镜像

Adapter 会先检查任务容器中的 `/tmp/mini-claude-py/.venv`。如果其中的 Python、`anthropic`、`openai`、`python-dotenv`、`rich` 和 `mini_claude` 都可导入，就跳过容器内的 apt/pip 安装；否则继续使用 fallback 安装流程。

从仓库根目录构建通用运行时镜像：

```powershell
docker build `
  -t mini-claude-agent:py313 `
  -f benchmark/images/mini-claude.Dockerfile `
  .

docker run --rm mini-claude-agent:py313 `
  /tmp/mini-claude-py/.venv/bin/python -c `
  "import mini_claude; print('ready')"
```

Terminal-Bench 任务通常还需要各自原始镜像中的工具和依赖，因此生产 benchmark 不应直接用这个通用镜像替换所有任务环境；应以任务原始镜像为 `FROM` 构建派生镜像，再在任务的 `[environment].docker_image` 中指定派生镜像。构建完成后，Harbor 会直接使用预构建镜像，任务环境中不再重复安装 Mini Claude。

如果不能直接修改 registry 任务的 `task.toml`，可以使用按任务选择镜像的自定义 Harbor environment。先复制 `benchmark/prebuilt-images.example.json` 为 `benchmark/prebuilt-images.json`，把任务短名改成实际的派生镜像 tag，然后运行：

```powershell
$env:MINI_CLAUDE_IMAGE_MAP = (Resolve-Path benchmark/prebuilt-images.json).Path
& "$env:CONDA_PREFIX\Scripts\harbor.exe" run `
  --env benchmark.prebuilt_environment:MiniClaudePrebuiltDockerEnvironment `
  --agent benchmark.harbor_agent:MiniClaudeHarborAgent `
  --dataset terminal-bench/terminal-bench-2-1 `
  --task terminal-bench/vulnerable-secret `
  --n-concurrent 1 `
  --env-file .env `
  --yes
```

映射只对列出的任务生效；没有映射的任务仍使用其原始 Dockerfile。派生镜像必须保留原任务镜像中的工具、文件和工作目录，并且不能使用 `--force-build`，否则 Harbor 会重新走 Dockerfile 构建流程。

REPL 命令：`/clear`、`/plan`、`/cost`、`/compact`、`/memory`、`/skills`、`/<skill名称>`。

## 架构

```
__main__.py  →  CLI 入口、argparse 参数解析、REPL 循环、会话恢复
agent.py     →  核心 Agent 循环：双后端 API 调用、流式输出、并行工具执行、
                4 层上下文压缩、计划模式编排、子 Agent 调度、预算控制
tools.py     →  13 个工具定义 + 执行 + 5 种权限模式 + mtime 先读后改保护
prompt.py    →  系统提示词模板（{{变量}}插值）、@include 指令解析、
                CLAUDE.md/rules 加载
session.py   →  JSON 文件持久化（~/.mini-claude/sessions/），保存/加载/列表
memory.py    →  4 类记忆（user/feedback/project/reference）、MEMORY.md 自动索引、
                sideQuery 语义召回、带门控的异步预取
skills.py    →  技能发现（.claude/skills/<名称>/SKILL.md）、inline 与 fork 两种执行模式、
                project/user 双来源（project 优先覆盖）
subagent.py  →  3 种内置类型（explore/plan/general）+ 自定义类型（.claude/agents/*.md）、
                fork-return 模式
mcp_client.py→  JSON-RPC over stdio、动态工具发现、带命名空间的工具（mcp__服务端__工具名）
ui.py        →  基于 rich 的终端输出、彩色 diff、spinner 动画
frontmatter.py→ 共享 YAML frontmatter 解析器，供 memory 和 skills 使用
```

### Agent Loop 流程

1. 构建系统提示词（CLAUDE.md、rules、记忆索引、技能、子 Agent、git 上下文）
2. 向 API 发送消息（Anthropic 或 OpenAI 兼容），开启流式输出
3. 解析响应：文本输出到 stdout，收集工具调用
4. 执行工具并检查权限 — 只读工具并行执行以加速
5. 追踪 token 消耗、检查预算、下一轮迭代注入召回的记忆
6. 接近上下文限制时自动压缩（4 层策略）
7. 退出时自动保存会话

### 权限系统（5 种模式）

| 模式 | 对应参数 | 行为 |
|------|---------|------|
| `default` | （无） | 危险 shell 命令、新建文件需确认 |
| `bypassPermissions` | `--yolo` | 跳过所有确认 |
| `plan` | `--plan` | 只读，仅允许写入计划文件 |
| `acceptEdits` | `--accept-edits` | 自动批准文件编辑，shell 仍需确认 |
| `dontAsk` | `--dont-ask` | 自动拒绝所有需确认的操作 |

声明式 allow/deny 规则通过 `.claude/settings.json` 和 `~/.claude/settings.json` 配置。

### 上下文压缩（4 层）

1. 预算截断 — 超出 token 预算时裁剪最早的消息
2. 过期剪除 — 移除仍在上下文中的旧工具结果
3. 微压缩 — 对中等规模的对话进行内联摘要
4. 自动压缩 — 接近限制时触发完整压缩，生成结构化摘要

超过 30KB 的工具结果会持久化到磁盘，上下文中仅保留预览。

### 记忆系统

- 存储路径：`~/.mini-claude/projects/<hash>/memory/*.md`，使用 YAML frontmatter
- 索引：写入时自动重新生成 `MEMORY.md`
- 召回：sideQuery 调用模型语义选择相关记忆，用户输入时异步预取（门控条件：多字输入、会话预算 < 60KB、存在记忆文件）
- 最多选择 5 条记忆，旧记忆附带时效性警告

### 技能系统

- 发现来源：`~/.claude/skills/`（用户级）和 `.claude/skills/`（项目级），项目级优先
- SKILL.md 格式：YAML frontmatter（name、description、when_to_use、user-invocable、context、allowed-tools）+ 提示词正文
- `inline` 上下文：解析后的提示词注入对话
- `fork` 上下文：作为子 Agent 运行，使用受限工具集

### Sub-Agent 类型

- `explore`：只读，快速代码库搜索（read_file、list_files、grep_search）
- `plan`：只读，结构化实现计划
- `general`：除 agent 外的完整工具集（不允许递归创建子 Agent）
- 自定义：在 `.claude/agents/<名称>.md` 中定义，YAML frontmatter 包含 `name`、`description`、`allowed-tools`

### MCP 集成

配置来源：`.mcp.json`、`.claude/settings.json` 或 `~/.claude/settings.json`。启动时自动发现工具，以 `mcp__<服务端>__<工具名>` 前缀暴露，实现命名空间隔离与路由。
