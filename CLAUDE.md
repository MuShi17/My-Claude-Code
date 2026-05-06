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
mini-claude-py --thinking                # 启用扩展思考（仅 Anthropic）
mini-claude-py --model <名称>            # 覆盖模型（环境变量：MINI_CLAUDE_MODEL）
mini-claude-py --api-base <URL>          # OpenAI 兼容 API 端点
mini-claude-py --max-cost 0.50           # 费用上限（美元）
mini-claude-py --max-turns 20            # 轮次上限

# Web 模式
mini-claude-py --web              # 启动浏览器界面（localhost:8000）
mini-claude-py --web --port 3000  # 自定义端口

# TypeScript 版（独立使用）
npm install && npm run build
npm start [-- --yolo --plan ...]
```

API 配置：设置 `ANTHROPIC_API_KEY`（Anthropic 格式，推荐）或 `OPENAI_API_KEY` + `OPENAI_BASE_URL`（OpenAI 兼容格式）。两者均支持自定义 base URL。

REPL 命令：`/clear`、`/plan`、`/cost`、`/compact`、`/memory`、`/skills`、`/<skill名称>`。

## Web 界面

通过 `--web` 启动 FastAPI 驱动的 Web 前端：

- **聊天页面** (`/`)：双栏布局（侧边栏会话列表 + 聊天区），SSE 流式输出，工具调用卡片（可折叠），文件 diff 高亮
- **思考内容**：可折叠的"思考过程"区域，与正式回答分层显示（支持 Anthropic thinking 和 DeepSeek reasoning_content）
- **管理面板** (`/admin`)：会话管理（恢复/删除）、记忆管理（查看/创建/删除）、技能列表、配置查看
- **会话恢复**：侧边栏点击历史会话即可恢复对话，继续发送消息自动写回同一会话文件
- **控制按钮**：输入区 ■ 按钮终止当前 Agent 处理，顶栏 关闭 按钮终止整个服务进程
- **主题**：CSS 变量驱动，`prefers-color-scheme` 自动跟随系统亮/暗模式
- **响应式**：≤768px 侧边栏自动隐藏，汉堡菜单唤出

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
web/__init__.py →  FastAPI app 工厂、静态文件 serve、路由注册
web/api.py      →  14 个 API 端点：聊天 SSE 流、会话/记忆/技能 CRUD、配置
web/models.py   →  Pydantic 请求/响应模型
web/templates/  →  index.html（聊天页面）、admin.html（管理面板）、style.css、app.js
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
