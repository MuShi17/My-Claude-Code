# Mini Claude Code

精简的编程智能体，复现 Claude Code 的核心架构。支持 CLI REPL 和 Web 前端两种交互方式。

## 快速开始

```bash
# 安装
cd src && pip install -e .

# 交互式 REPL
mini-claude-py

# One-shot 模式
mini-claude-py "修复 src/tools.py 的编码问题"

# Web 前端
mini-claude-py --web                # http://localhost:8000
mini-claude-py --web --port 3000    # 自定义端口
```

## API 配置

设置环境变量（二选一）：

- **Anthropic**：`ANTHROPIC_API_KEY`（可选 `ANTHROPIC_BASE_URL`）
- **OpenAI 兼容**：`OPENAI_API_KEY` + `OPENAI_BASE_URL`（如 DeepSeek、OpenRouter 等）

```bash
OPENAI_API_KEY=sk-xxx mini-claude-py --api-base https://aihubmix.com/v1 --model gpt-4o "hello"
```

## 权限模式

| 参数 | 模式 | 行为 |
|------|------|------|
| （无） | `default` | 危险操作需确认 |
| `--yolo` | `bypassPermissions` | 跳过所有确认 |
| `--plan` | `plan` | 只读，仅允许写入计划文件 |
| `--accept-edits` | `acceptEdits` | 自动批准文件编辑 |
| `--dont-ask` | `dontAsk` | 自动拒绝所有确认 |

## REPL 命令

`/clear` `/plan` `/cost` `/compact` `/memory` `/skills` `/<skill名称>`

## Web 界面特性

- **聊天页面**：双栏布局（会话列表 + 聊天区），SSE 流式输出，工具调用卡片
- **思考过程**：可折叠展示（支持 Anthropic thinking 和 DeepSeek reasoning_content）
- **管理面板**：会话管理、记忆管理、技能列表、配置查看
- **会话恢复**：历史会话一键恢复，继续对话自动归入同一会话
- **亮/暗主题**：自动跟随系统设置

## 架构

```
agent.py      核心循环：双后端 API、流式输出、并行工具、上下文压缩
tools.py      13 个工具 + 5 种权限模式
prompt.py     系统提示词模板、CLAUDE.md/rules 加载
session.py    会话持久化（JSON）
memory.py     4 类记忆 + 语义召回
skills.py     技能系统（inline/fork 双模式）
subagent.py   子 Agent（explore/plan/general）
mcp_client.py MCP 协议集成
web/          FastAPI Web 前端（SSE + REST API + HTML/JS）
ui.py         CLI 终端输出（rich）
```
