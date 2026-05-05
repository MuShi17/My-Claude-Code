# FastAPI Web 前端 — 设计文档

**日期**: 2026-05-05
**状态**: 已确认

## 概述

基于 FastAPI 为 mini-claude-code 增加 Web 聊天界面 + 管理面板，替代纯终端 REPL 体验。

## 设计原则

- 进程内嵌：FastAPI 同时 serve API 和静态页面，单进程部署
- 原生 JS + SSE：流式聊天逐字输出，无前端构建工具
- 本地单用户：localhost 使用，无需认证/会话隔离
- Demo 友好：代码结构清晰，一眼看懂全貌

## 架构

```
src/mini_claude/web/
├── __init__.py          # create_app() 工厂函数，注册路由和静态文件
├── api.py               # 所有 API 路由
├── models.py            # Pydantic 请求/响应模型
└── templates/
    ├── index.html        # 聊天主页面
    ├── admin.html        # 管理面板页面
    ├── style.css         # 全局样式
    └── app.js            # 前端逻辑（SSE 消费、UI 更新）
```

**修改文件：**
- `src/mini_claude/__main__.py`：新增 `--web` 和 `--port` 参数
- `src/pyproject.toml`：新增 `fastapi`、`uvicorn` 依赖

**关键设计决策：**
- Agent 实例在 app 启动时创建，单例模式
- 页面路由直接 serve HTML 文件，不做服务端模板渲染
- API 路由统一前缀 `/api/*`

## API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/chat | 聊天入口，返回 SSE 流 |
| GET | /api/sessions | 会话列表 |
| POST | /api/sessions/{id}/resume | 恢复会话 |
| DELETE | /api/sessions/{id} | 删除会话 |
| GET | /api/memories | 记忆列表 |
| POST | /api/memories | 新增记忆 |
| DELETE | /api/memories/{name} | 删除记忆 |
| GET | /api/skills | 技能列表 |
| GET | /api/cost | token 消耗统计 |
| POST | /api/compact | 触发压缩 |
| POST | /api/confirm | 权限确认回调 |
| GET | /api/status | Agent 就绪状态 |

## SSE 事件协议

| type | data | 说明 |
|------|------|------|
| `text` | 字符串 | 模型输出的文本片段（逐 token） |
| `tool` | `{name, input}` | 工具调用开始 |
| `result` | `{tool, content}` | 工具执行结果（>500 字符截断） |
| `confirm` | `{command, id}` | 需要用户确认危险操作 |
| `diff` | `{file, lines}` | 文件编辑 diff 展示 |
| `error` | 字符串 | 异常/错误信息 |
| `cost` | `{input, output}` | 本轮 token 消耗 |
| `done` | null | 本轮对话结束 |

### 权限交互流程

```
服务端 → SSE confirm 事件 → 前端弹窗 → 用户点击
→ POST /api/confirm {id, approved: true/false} → 服务端继续
```

Agent 的 `confirm_fn` 改为 await 等待前端 HTTP 返回。

## 页面设计

### 聊天主页面 (`/`)

- 消息区域：用户消息 + AI 回复 + 工具调用卡片
- 流式逐字渲染（打字机效果）
- 工具调用可折叠展开
- 文件 diff 彩色高亮（红/绿行）
- 权限确认弹窗
- 底部输入区 + 快捷命令按钮（/clear /compact /cost /plan）
- 底部状态栏（Tokens / Cost）
- 暗色终端风格主题

### 管理面板 (`/admin`)

4 个 Tab：

- **会话**：列表，恢复/删除操作，token 和 cost 信息
- **记忆**：列表，查看内容/删除操作
- **技能**：列表，显示名称/描述/来源/是否可调用
- **配置**：只读展示当前 model、permission mode 等

## 错误处理

| 场景 | 前端表现 |
|------|---------|
| Agent 未就绪（无 API key） | 顶部红色横幅 + `/api/status` 检查 |
| SSE 连接中断 | 自动重连 + toast 提示 |
| Tool 执行异常 | SSE error 事件，前端红色展示 |
| API 限流过载 | 复用 Agent 内置指数退避，前端 spinner |
| 会话文件损坏 | 列表中灰色标记"不可恢复" |

## 启动方式

```bash
mini-claude-py --web              # localhost:8000
mini-claude-py --web --port 3000  # 自定义端口
```

## 规划

下一步：实现计划（writing-plans skill）
