# FastAPI Web 前端 — 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 mini-claude-code 添加基于 FastAPI 的 Web 聊天界面 + 管理面板。

**Architecture:** FastAPI 内嵌静态页面，单进程 serve HTML + SSE 流式 API。Agent 单例在 app 启动时创建，`confirm_fn` 改为 HTTP 回调。前端原生 JS，SSE 消费逐字渲染。

**Tech Stack:** FastAPI, uvicorn, Server-Sent Events (SSE), vanilla HTML/CSS/JS, Pydantic

**Design doc:** `docs/plans/2026-05-05-fastapi-web-frontend-design.md`

---

### Task 1: 添加依赖

**Files:**
- Modify: `src/pyproject.toml`

**Step 1: 修改 pyproject.toml，添加 fastapi 和 uvicorn 依赖**

```toml
[project]
name = "mini-claude-py"
version = "0.1.0"
description = "A concise programming agent mimicking Claude Code"
readme = "README.md"
requires-python = ">=3.8"
dependencies = [
    "fastapi>=0.100.0",
    "uvicorn[standard]>=0.23.0",
]
```

**Step 2: 安装依赖并验证**

```bash
cd src && pip install -e .
```

Expected: fastapi 和 uvicorn 及其依赖安装成功，`mini-claude-py` 命令仍可正常使用。

**Step 3: Commit**

```bash
git add src/pyproject.toml
git commit -m "chore: add fastapi and uvicorn dependencies"
```

---

### Task 2: 创建 web 模块骨架

**Files:**
- Create: `src/mini_claude/web/__init__.py`
- Create: `src/mini_claude/web/models.py`

**Step 1: 创建 `__init__.py` — FastAPI app 工厂函数**

```python
"""Web frontend for Mini Claude Code — FastAPI app factory."""

from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(agent) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        agent: A pre-configured Agent instance (singleton for the web session).
    """
    app = FastAPI(title="Mini Claude Code Web")

    # Inject agent into app state for route access
    app.state.agent = agent

    # Import and register API routes
    from .api import router
    app.include_router(router, prefix="/api")

    # Serve the main chat page
    @app.get("/")
    async def index():
        from fastapi.responses import HTMLResponse
        content = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content)

    # Serve the admin panel
    @app.get("/admin")
    async def admin():
        from fastapi.responses import HTMLResponse
        content = (_TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8")
        return HTMLResponse(content)

    return app
```

**Step 2: 创建 `models.py` — Pydantic 请求/响应模型**

```python
"""Pydantic models for the web API."""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    id: str
    approved: bool


class MemoryCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "project"
    content: str


class SessionItem(BaseModel):
    id: str
    model: str
    cwd: str
    start_time: str
    message_count: int
    valid: bool = True


class MemoryItem(BaseModel):
    name: str
    description: str
    type: str
    filename: str


class SkillItem(BaseModel):
    name: str
    description: str
    source: str
    user_invocable: bool
    context: str


class AgentStatus(BaseModel):
    ready: bool
    model: str
    permission_mode: str
    session_id: str


class CostInfo(BaseModel):
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    max_cost_usd: float | None = None
    turns: int = 0
    max_turns: int | None = None
```

**Step 3: Commit**

```bash
git add src/mini_claude/web/__init__.py src/mini_claude/web/models.py
git commit -m "feat: add web module skeleton with FastAPI app factory and models"
```

---

### Task 3: 创建 API 路由 — 核心聊天 SSE

**Files:**
- Create: `src/mini_claude/web/api.py`

**Step 1: 创建 api.py — 聊天 SSE 端点和管理端点骨架**

```python
"""API routes for the Mini Claude Code web frontend."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from .models import (
    ChatRequest, ConfirmRequest, MemoryCreate,
    SessionItem, MemoryItem, SkillItem, AgentStatus, CostInfo,
)

router = APIRouter()

# ─── In-flight confirmations ───────────────────────────────
# 当 Agent 需要确认危险操作时，SSE 推送 confirm 事件，
# 前端的确认结果写入此 dict，Agent 端 await 等待。
_pending_confirms: dict[str, asyncio.Future] = {}


def _get_agent(request: Request):
    return request.app.state.agent


# ─── SSE helpers ─────────────────────────────────────────────

def _sse_event(event_type: str, data: dict | str | None = None) -> dict:
    """Build an SSE event dict for EventSourceResponse."""
    return {"event": event_type, "data": json.dumps(data, ensure_ascii=False) if data is not None else ""}


# ─── POST /api/chat ─────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Main chat endpoint. Returns an SSE stream of agent responses."""
    agent = _get_agent(request)

    if agent.is_processing:
        return StreamingResponse(
            iter([f"data: {json.dumps({'error': 'Agent is busy'})}\n\n"]),
            media_type="text/event-stream",
        )

    async def event_stream():
        # Override agent's _emit_text to push SSE text events
        original_emit = agent._emit_text

        def _web_emit(text: str):
            """Intercept _emit_text to push SSE instead of stdout."""
            _queue.append(("text", text))

        agent._emit_text = _web_emit

        # Override confirm_fn to await web confirmation
        original_confirm = agent.confirm_fn

        async def _web_confirm(message: str) -> bool:
            cid = uuid.uuid4().hex[:8]
            _queue.append(("confirm", {"id": cid, "command": message}))
            future: asyncio.Future = asyncio.get_event_loop().create_future()
            _pending_confirms[cid] = future
            try:
                result = await asyncio.wait_for(future, timeout=300)
                return result
            except asyncio.TimeoutError:
                _pending_confirms.pop(cid, None)
                return False

        agent.confirm_fn = _web_confirm

        # Collect SSE events via queue
        _queue: list[tuple[str, any]] = []

        # Run agent in background task
        chat_task = asyncio.create_task(agent.chat(req.message))

        # Override print_tool_call / print_tool_result via monkey-patching
        # We hook into agent's tool execution by patching the ui module functions
        import mini_claude.ui as ui_mod

        _orig_tool_call = ui_mod.print_tool_call
        _orig_tool_result = ui_mod.print_tool_result
        _orig_cost = ui_mod.print_cost

        def _patched_tool_call(name: str, inp: dict):
            _queue.append(("tool", {"name": name, "input": inp}))

        def _patched_tool_result(name: str, result: str):
            max_len = 500
            display = result[:max_len] + f"\n... ({len(result)} chars total)" if len(result) > max_len else result
            # Check if this is an edit/write result — extract diff info
            if name in ("edit_file", "write_file") and not result.startswith("Error"):
                lines = result.split("\n")
                _queue.append(("diff", {"tool": name, "file": lines[0] if lines else "", "lines": lines[1:41]}))
            else:
                _queue.append(("result", {"tool": name, "content": display}))

        def _patched_cost(input_tokens: int, output_tokens: int):
            _queue.append(("cost", {"input": input_tokens, "output": output_tokens}))

        ui_mod.print_tool_call = _patched_tool_call
        ui_mod.print_tool_result = _patched_tool_result
        ui_mod.print_cost = _patched_cost

        try:
            # Stream SSE events while agent is running
            last_check = 0
            while not chat_task.done() or _queue:
                # Drain queue
                while _queue:
                    evt_type, evt_data = _queue.pop(0)
                    yield _sse_event(evt_type, evt_data)

                if chat_task.done():
                    # Drain any remaining events
                    while _queue:
                        evt_type, evt_data = _queue.pop(0)
                        yield _sse_event(evt_type, evt_data)
                    break

                await asyncio.sleep(0.05)

            # Check for exceptions
            if chat_task.exception():
                yield _sse_event("error", str(chat_task.exception()))

            # Push final cost
            tokens = agent.get_token_usage()
            cost_in = (tokens["input"] / 1_000_000) * 3
            cost_out = (tokens["output"] / 1_000_000) * 15
            yield _sse_event("cost", {
                "input": tokens["input"],
                "output": tokens["output"],
                "estimated_cost_usd": round(cost_in + cost_out, 4),
            })
            yield _sse_event("done")

        except Exception as e:
            yield _sse_event("error", str(e))
        finally:
            # Restore original functions
            agent._emit_text = original_emit
            agent.confirm_fn = original_confirm
            ui_mod.print_tool_call = _orig_tool_call
            ui_mod.print_tool_result = _orig_tool_result
            ui_mod.print_cost = _orig_cost

    return EventSourceResponse(event_stream())


# ─── POST /api/confirm ──────────────────────────────────────

@router.post("/confirm")
async def confirm(req: ConfirmRequest):
    future = _pending_confirms.get(req.id)
    if future and not future.done():
        future.set_result(req.approved)
        return {"ok": True}
    return {"ok": False, "error": "Unknown or expired confirmation id"}
```

**Step 2: Commit**

```bash
git add src/mini_claude/web/api.py
git commit -m "feat: add SSE chat endpoint with confirmation flow"
```

---

### Task 4: 完善 API 路由 — 管理端点

**Files:**
- Modify: `src/mini_claude/web/api.py`

**Step 1: 在 api.py 末尾追加状态、会话、记忆、技能、配置端点**

```python
# ─── GET /api/status ────────────────────────────────────────

@router.get("/status", response_model=AgentStatus)
async def agent_status(request: Request):
    agent = _get_agent(request)
    return AgentStatus(
        ready=not agent.is_processing,
        model=agent.model,
        permission_mode=agent.permission_mode,
        session_id=agent.session_id,
    )


# ─── GET /api/cost ──────────────────────────────────────────

@router.get("/cost", response_model=CostInfo)
async def cost(request: Request):
    agent = _get_agent(request)
    tokens = agent.get_token_usage()
    cost_in = (tokens["input"] / 1_000_000) * 3
    cost_out = (tokens["output"] / 1_000_000) * 15
    return CostInfo(
        input_tokens=tokens["input"],
        output_tokens=tokens["output"],
        estimated_cost_usd=round(cost_in + cost_out, 4),
        max_cost_usd=agent.max_cost_usd,
        turns=agent.current_turns,
        max_turns=agent.max_turns,
    )


# ─── POST /api/compact ──────────────────────────────────────

@router.post("/compact")
async def compact(request: Request):
    agent = _get_agent(request)
    await agent.compact()
    return {"ok": True}


# ─── POST /api/clear ────────────────────────────────────────

@router.post("/clear")
async def clear_history(request: Request):
    agent = _get_agent(request)
    agent.clear_history()
    return {"ok": True}


# ─── POST /api/plan/toggle ──────────────────────────────────

@router.post("/plan/toggle")
async def toggle_plan(request: Request):
    agent = _get_agent(request)
    mode = agent.toggle_plan_mode()
    return {"mode": mode}


# ─── Sessions ────────────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionItem])
async def list_sessions():
    from ..session import list_sessions as _list_sessions, SESSION_DIR
    sessions = _list_sessions()
    result = []
    for s in sessions:
        sid = s.get("id", "")
        valid = (SESSION_DIR / f"{sid}.json").exists()
        result.append(SessionItem(
            id=sid,
            model=s.get("model", ""),
            cwd=s.get("cwd", ""),
            start_time=s.get("startTime", ""),
            message_count=s.get("messageCount", 0),
            valid=valid,
        ))
    result.sort(key=lambda x: x.start_time, reverse=True)
    return result


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str, request: Request):
    from ..session import load_session
    agent = _get_agent(request)
    data = load_session(session_id)
    if not data:
        return {"ok": False, "error": "Session not found"}
    agent.restore_session(data)
    return {"ok": True}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    from ..session import SESSION_DIR
    path = SESSION_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()
        return {"ok": True}
    return {"ok": False, "error": "Session not found"}


# ─── Memories ────────────────────────────────────────────────

@router.get("/memories", response_model=list[MemoryItem])
async def list_memories_api():
    from ..memory import list_memories
    memories = list_memories()
    return [
        MemoryItem(name=m.name, description=m.description, type=m.type, filename=m.filename)
        for m in memories
    ]


@router.get("/memories/{filename}")
async def get_memory_content(filename: str):
    from ..memory import get_memory_dir
    path = get_memory_dir() / filename
    if not path.exists():
        return {"ok": False, "error": "Memory not found"}
    content = path.read_text(encoding="utf-8")
    return {"ok": True, "filename": filename, "content": content}


@router.post("/memories")
async def create_memory(req: MemoryCreate):
    from ..memory import save_memory
    filename = save_memory(req.name, req.description, req.type, req.content)
    return {"ok": True, "filename": filename}


@router.delete("/memories/{filename}")
async def delete_memory_api(filename: str):
    from ..memory import delete_memory
    ok = delete_memory(filename)
    return {"ok": ok}


# ─── Skills ──────────────────────────────────────────────────

@router.get("/skills", response_model=list[SkillItem])
async def list_skills_api():
    from ..skills import discover_skills
    skills = discover_skills()
    return [
        SkillItem(
            name=s.name, description=s.description,
            source=s.source, user_invocable=s.user_invocable, context=s.context,
        )
        for s in skills
    ]
```

**Step 2: Commit**

```bash
git add src/mini_claude/web/api.py
git commit -m "feat: add management API endpoints (sessions, memories, skills, config)"
```

---

### Task 5: 创建前端 — 样式和 JS 逻辑

**Files:**
- Create: `src/mini_claude/web/templates/style.css`
- Create: `src/mini_claude/web/templates/app.js`

**Step 1: 创建 style.css**

```css
/* Mini Claude Code Web — Dark terminal theme */

:root {
    --bg: #1a1b26;
    --surface: #24283b;
    --border: #414868;
    --text: #c0caf5;
    --dim: #565f89;
    --accent: #7aa2f7;
    --green: #9ece6a;
    --red: #f7768e;
    --yellow: #e0af68;
    --cyan: #7dcfff;
    --magenta: #bb9af7;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
    font-size: 14px;
    line-height: 1.5;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* Header */
header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 8px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-shrink: 0;
}
header .title { color: var(--cyan); font-weight: bold; }
header .nav a { color: var(--dim); text-decoration: none; margin-left: 16px; }
header .nav a:hover { color: var(--accent); }
header .status-bar { font-size: 12px; color: var(--dim); }

/* Banner */
.banner { padding: 8px 16px; text-align: center; flex-shrink: 0; }
.banner.error { background: var(--red); color: #fff; }

/* Messages area */
#messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
}
.message { max-width: 85%; padding: 10px 14px; border-radius: 8px; }
.message.user { align-self: flex-end; background: var(--accent); color: #fff; }
.message.assistant { align-self: flex-start; background: var(--surface); border: 1px solid var(--border); }

/* Tool call card */
.tool-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin: 8px 0;
    overflow: hidden;
}
.tool-card .tool-header {
    padding: 6px 10px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
    background: rgba(0,0,0,0.2);
    font-size: 13px;
}
.tool-card .tool-header:hover { background: rgba(0,0,0,0.3); }
.tool-card .tool-icon { font-size: 14px; }
.tool-card .tool-name { color: var(--yellow); }
.tool-card .tool-summary { color: var(--dim); font-size: 12px; }
.tool-card .tool-body { padding: 8px 12px; display: none; font-size: 13px; max-height: 300px; overflow-y: auto; }
.tool-card.open .tool-body { display: block; }

/* Diff display */
.diff-line { white-space: pre-wrap; font-size: 13px; line-height: 1.4; }
.diff-line.add { color: var(--green); }
.diff-line.remove { color: var(--red); }
.diff-line.context { color: var(--dim); }

/* Confirm modal */
.modal-overlay {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 100;
}
.modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    max-width: 500px;
    width: 90%;
}
.modal h3 { color: var(--yellow); margin-bottom: 12px; }
.modal .cmd { color: var(--text); background: var(--bg); padding: 8px; border-radius: 4px; font-size: 13px; margin-bottom: 16px; word-break: break-all; }
.modal .actions { display: flex; gap: 10px; justify-content: flex-end; }

/* Buttons */
.btn {
    padding: 6px 16px; border: 1px solid var(--border); border-radius: 4px;
    cursor: pointer; font-family: inherit; font-size: 13px; background: var(--surface); color: var(--text);
}
.btn:hover { opacity: 0.8; }
.btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn.danger { background: var(--red); color: #fff; border-color: var(--red); }

/* Input area */
#input-area {
    border-top: 1px solid var(--border);
    padding: 12px 16px;
    background: var(--surface);
    flex-shrink: 0;
}
#input-area .shortcuts { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
#input-area .shortcut {
    font-size: 12px; color: var(--dim); background: var(--bg);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 8px; cursor: pointer;
}
#input-area .shortcut:hover { color: var(--accent); border-color: var(--accent); }
#input-area .row { display: flex; gap: 8px; }
#input-area textarea {
    flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 4px; padding: 8px; font-family: inherit; font-size: 14px;
    resize: none; min-height: 40px; max-height: 120px;
}
#input-area textarea:focus { outline: none; border-color: var(--accent); }

/* Admin page */
.admin-tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.admin-tab {
    padding: 8px 16px; cursor: pointer; color: var(--dim);
    border-bottom: 2px solid transparent; font-size: 14px;
}
.admin-tab:hover { color: var(--text); }
.admin-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.admin-content { flex: 1; overflow-y: auto; padding: 16px; }
.admin-content .tab-panel { display: none; }
.admin-content .tab-panel.active { display: block; }

/* Cards in admin */
.item-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 12px; margin-bottom: 8px;
    display: flex; justify-content: space-between; align-items: center;
}
.item-card .info { flex: 1; }
.item-card .info .name { color: var(--accent); font-size: 14px; }
.item-card .info .meta { color: var(--dim); font-size: 12px; margin-top: 4px; }
.item-card .actions { display: flex; gap: 8px; flex-shrink: 0; }
.item-card.invalid { opacity: 0.4; }

/* Toast */
#toast {
    position: fixed; bottom: 80px; right: 16px;
    background: var(--surface); border: 1px solid var(--border);
    padding: 10px 16px; border-radius: 6px; font-size: 13px;
    z-index: 50; display: none;
}
#toast.show { display: block; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Spinner */
.spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
```

**Step 2: 创建 app.js**

```javascript
// Mini Claude Code Web — Frontend logic

const API = {
    async chat(message) {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });
        return resp.body.getReader();
    },
    async confirm(id, approved) {
        const resp = await fetch('/api/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, approved }),
        });
        return resp.json();
    },
    async fetchJSON(url, opts = {}) {
        const resp = await fetch(url, opts);
        return resp.json();
    },
};

// ─── Chat page ──────────────────────────────────────────────

let _currentAssistantDiv = null;
let _toolCardMap = {}; // tool_use_id -> card element

function initChat() {
    const input = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Shortcut buttons
    document.querySelectorAll('.shortcut').forEach(btn => {
        btn.addEventListener('click', () => {
            const cmd = btn.dataset.cmd;
            if (cmd === '/plan') {
                fetch('/api/plan/toggle', { method: 'POST' });
                addToast('Plan mode toggled');
            } else {
                fetch(`/api/${cmd.replace('/', '')}`, { method: 'POST' })
                    .then(r => r.json())
                    .then(d => { if (d.ok !== false) addToast(`${cmd} done`); });
            }
        });
    });

    // Check agent status on load
    fetch('/api/status').then(r => r.json()).then(s => {
        if (!s.ready) {
            showBanner('Agent is not ready — check API key configuration', 'error');
        }
        updateStatusBar(s);
    });
}

async function sendMessage() {
    const input = document.getElementById('user-input');
    const message = input.value.trim();
    if (!message) return;
    input.value = '';

    // Add user message bubble
    appendMessage('user', message);

    // Create assistant message container
    _currentAssistantDiv = appendMessage('assistant', '');
    _toolCardMap = {};

    const reader = await API.chat(message);
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        let currentEvent = null;

        for (const line of lines) {
            if (line.startsWith('event: ')) {
                currentEvent = line.slice(7).trim();
            } else if (line.startsWith('data: ') && currentEvent) {
                try {
                    const data = JSON.parse(line.slice(6));
                    handleSSE(currentEvent, data);
                } catch (e) {
                    // skip malformed
                }
                currentEvent = null;
            }
        }
    }

    _currentAssistantDiv = null;
    updateStatus();
}

function handleSSE(type, data) {
    switch (type) {
        case 'text':
            appendText(data);
            break;
        case 'tool':
            addToolCard(data.name, data.input);
            break;
        case 'result':
            updateToolResult(data.tool, data.content);
            break;
        case 'diff':
            updateToolDiff(data.tool, data.file, data.lines);
            break;
        case 'confirm':
            showConfirmModal(data.id, data.command);
            break;
        case 'error':
            appendError(data);
            break;
        case 'cost':
            updateStatusBarCost(data);
            break;
        case 'done':
            break;
    }
}

function appendMessage(role, text) {
    const msgs = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (text) div.textContent = text;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return div;
}

function appendText(text) {
    if (_currentAssistantDiv) {
        // Replace spinner if present
        const spinner = _currentAssistantDiv.querySelector('.spinner');
        if (spinner) spinner.remove();
        _currentAssistantDiv.textContent += text;
        document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
    }
}

function addToolCard(name, input) {
    if (!_currentAssistantDiv) return;
    const msgs = document.getElementById('messages');

    const summary = getToolSummary(name, input);
    const card = document.createElement('div');
    card.className = 'tool-card';
    card.innerHTML = `
        <div class="tool-header" onclick="this.parentElement.classList.toggle('open')">
            <span class="tool-icon">${getToolIcon(name)}</span>
            <span class="tool-name">${name}</span>
            <span class="tool-summary">${summary}</span>
            <span class="spinner" style="margin-left:auto"></span>
        </div>
        <div class="tool-body"><pre style="color:var(--dim)">Running...</pre></div>
    `;
    msgs.appendChild(card);
    msgs.scrollTop = msgs.scrollHeight;
    return card;
}

function updateToolResult(toolName, content) {
    const cards = document.querySelectorAll('.tool-card');
    const card = cards[cards.length - 1];
    if (card) {
        const body = card.querySelector('.tool-body');
        body.innerHTML = `<pre style="white-space:pre-wrap;color:var(--dim);font-size:12px">${escapeHtml(content)}</pre>`;
        const spinner = card.querySelector('.spinner');
        if (spinner) spinner.remove();
    }
}

function updateToolDiff(toolName, file, lines) {
    const cards = document.querySelectorAll('.tool-card');
    const card = cards[cards.length - 1];
    if (card) {
        const body = card.querySelector('.tool-body');
        let html = `<div style="color:var(--dim);font-size:12px">${escapeHtml(file)}</div>`;
        for (const line of lines) {
            let cls = 'context';
            if (line.startsWith('+ ')) cls = 'add';
            else if (line.startsWith('- ')) cls = 'remove';
            else if (line.startsWith('@@')) cls = 'context';
            html += `<div class="diff-line ${cls}">${escapeHtml(line)}</div>`;
        }
        body.innerHTML = html;
        const spinner = card.querySelector('.spinner');
        if (spinner) spinner.remove();
    }
}

function showConfirmModal(id, command) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
            <div class="modal">
                <h3>Dangerous Command</h3>
                <div class="cmd">${escapeHtml(command)}</div>
                <div class="actions">
                    <button class="btn danger deny">Deny</button>
                    <button class="btn primary allow">Allow</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector('.allow').addEventListener('click', async () => {
            document.body.removeChild(overlay);
            await API.confirm(id, true);
        });
        overlay.querySelector('.deny').addEventListener('click', async () => {
            document.body.removeChild(overlay);
            await API.confirm(id, false);
        });
    });
}

function appendError(text) {
    if (_currentAssistantDiv) {
        const span = document.createElement('span');
        span.style.color = 'var(--red)';
        span.textContent = '\n' + text;
        _currentAssistantDiv.appendChild(span);
    }
}

function showBanner(msg, level) {
    const banner = document.getElementById('banner');
    if (banner) {
        banner.textContent = msg;
        banner.className = `banner ${level}`;
        banner.style.display = 'block';
    }
}

function addToast(msg) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = 'show';
    setTimeout(() => { toast.className = ''; }, 2000);
}

async function updateStatus() {
    try {
        const s = await API.fetchJSON('/api/status');
        updateStatusBar(s);
    } catch(e) {}
}

function updateStatusBar(s) {
    const el = document.getElementById('status-info');
    if (el) el.textContent = `${s.model} | ${s.permission_mode} | ${s.session_id}`;
}

function updateStatusBarCost(data) {
    const el = document.getElementById('status-info');
    if (el) {
        const cost = ((data.input / 1_000_000) * 3 + (data.output / 1_000_000) * 15).toFixed(4);
        el.textContent = `Tokens: ${data.input}/${data.output} | $${cost}`;
    }
}

function getToolIcon(name) {
    const icons = { read_file: '📖', write_file: '✏️', edit_file: '🔧', list_files: '📁', grep_search: '🔍', run_shell: '💻', skill: '⚡', agent: '🤖' };
    return icons[name] || '🔨';
}

function getToolSummary(name, input) {
    if (name === 'read_file' || name === 'write_file' || name === 'edit_file') return input.file_path || '';
    if (name === 'run_shell') return (input.command || '').slice(0, 60);
    if (name === 'grep_search') return `"${input.pattern || ''}" in ${input.path || '.'}`;
    return '';
}

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ─── Admin page ─────────────────────────────────────────────

function initAdmin() {
    // Tab switching
    document.querySelectorAll('.admin-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
        });
    });

    // Load data
    loadSessions();
    loadMemories();
    loadSkills();
    loadConfig();
}

async function loadSessions() {
    try {
        const sessions = await API.fetchJSON('/api/sessions');
        const container = document.getElementById('tab-sessions');
        if (!sessions.length) {
            container.innerHTML = '<p style="color:var(--dim)">No sessions found.</p>';
            return;
        }
        container.innerHTML = sessions.map(s => `
            <div class="item-card ${s.valid ? '' : 'invalid'}">
                <div class="info">
                    <div class="name">${s.id}</div>
                    <div class="meta">${s.start_time} | ${s.model} | ${s.cwd} | ${s.message_count} msgs</div>
                </div>
                <div class="actions">
                    <button class="btn" onclick="resumeSession('${s.id}')">Resume</button>
                    <button class="btn danger" onclick="deleteSession('${s.id}')">Delete</button>
                </div>
            </div>
        `).join('');
    } catch(e) { console.error(e); }
}

async function resumeSession(id) {
    const r = await API.fetchJSON(`/api/sessions/${id}/resume`, { method: 'POST' });
    if (r.ok) addToast('Session resumed');
    else addToast('Failed: ' + (r.error || 'unknown'));
}

async function deleteSession(id) {
    await API.fetchJSON(`/api/sessions/${id}`, { method: 'DELETE' });
    loadSessions();
}

async function loadMemories() {
    try {
        const memories = await API.fetchJSON('/api/memories');
        const container = document.getElementById('tab-memories');
        if (!memories.length) {
            container.innerHTML = '<p style="color:var(--dim)">No memories saved.</p>';
            return;
        }
        container.innerHTML = memories.map(m => `
            <div class="item-card">
                <div class="info">
                    <div class="name">[${m.type}] ${m.name}</div>
                    <div class="meta">${m.description} | ${m.filename}</div>
                </div>
                <div class="actions">
                    <button class="btn" onclick="viewMemory('${m.filename}')">View</button>
                    <button class="btn danger" onclick="deleteMemory('${m.filename}')">Delete</button>
                </div>
            </div>
        `).join('');
    } catch(e) { console.error(e); }
}

async function viewMemory(filename) {
    const r = await API.fetchJSON(`/api/memories/${filename}`);
    if (r.ok) {
        alert(r.content);
    }
}

async function deleteMemory(filename) {
    await API.fetchJSON(`/api/memories/${filename}`, { method: 'DELETE' });
    loadMemories();
}

async function loadSkills() {
    try {
        const skills = await API.fetchJSON('/api/skills');
        const container = document.getElementById('tab-skills');
        if (!skills.length) {
            container.innerHTML = '<p style="color:var(--dim)">No skills found.</p>';
            return;
        }
        container.innerHTML = skills.map(s => `
            <div class="item-card">
                <div class="info">
                    <div class="name">${s.user_invocable ? '/' + s.name : s.name}</div>
                    <div class="meta">${s.description} | source: ${s.source} | context: ${s.context}</div>
                </div>
            </div>
        `).join('');
    } catch(e) { console.error(e); }
}

async function loadConfig() {
    try {
        const status = await API.fetchJSON('/api/status');
        const cost = await API.fetchJSON('/api/cost');
        const container = document.getElementById('tab-config');
        container.innerHTML = `
            <div class="item-card">
                <div class="info">
                    <div class="name">Model</div>
                    <div class="meta">${status.model}</div>
                </div>
            </div>
            <div class="item-card">
                <div class="info">
                    <div class="name">Permission Mode</div>
                    <div class="meta">${status.permission_mode}</div>
                </div>
            </div>
            <div class="item-card">
                <div class="info">
                    <div class="name">Session ID</div>
                    <div class="meta">${status.session_id}</div>
                </div>
            </div>
            <div class="item-card">
                <div class="info">
                    <div class="name">Tokens</div>
                    <div class="meta">Input: ${cost.input_tokens} | Output: ${cost.output_tokens} | Cost: $${cost.estimated_cost_usd}</div>
                </div>
            </div>
            <div class="item-card">
                <div class="info">
                    <div class="name">Turns</div>
                    <div class="meta">${cost.turns}${cost.max_turns ? ` / ${cost.max_turns}` : ''}</div>
                </div>
            </div>
        `;
    } catch(e) { console.error(e); }
}
```

**Step 3: Commit**

```bash
git add src/mini_claude/web/templates/style.css src/mini_claude/web/templates/app.js
git commit -m "feat: add frontend CSS and JS for chat and admin pages"
```

---

### Task 6: 创建前端 — HTML 页面

**Files:**
- Create: `src/mini_claude/web/templates/index.html`
- Create: `src/mini_claude/web/templates/admin.html`

**Step 1: 创建 index.html — 聊天主页面**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mini Claude Code</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <header>
        <span class="title">Mini Claude Code Web</span>
        <div>
            <span class="status-bar" id="status-info">Loading...</span>
            <span class="nav"><a href="/admin">管理面板</a></span>
        </div>
    </header>

    <div id="banner" class="banner" style="display:none"></div>

    <div id="messages"></div>

    <div id="input-area">
        <div class="shortcuts">
            <span class="shortcut" data-cmd="/clear">/clear</span>
            <span class="shortcut" data-cmd="/compact">/compact</span>
            <span class="shortcut" data-cmd="/cost">/cost</span>
            <span class="shortcut" data-cmd="/plan">/plan</span>
        </div>
        <div class="row">
            <textarea id="user-input" rows="1" placeholder="输入你的问题..."></textarea>
            <button class="btn primary" id="send-btn">发送</button>
        </div>
    </div>

    <div id="toast"></div>

    <script src="/static/app.js"></script>
    <script>initChat();</script>
</body>
</html>
```

**Step 2: 创建 admin.html — 管理面板**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mini Claude Code — 管理面板</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <header>
        <span class="title">管理面板</span>
        <span class="nav"><a href="/">← 返回聊天</a></span>
    </header>

    <div class="admin-tabs">
        <div class="admin-tab active" data-tab="sessions">会话</div>
        <div class="admin-tab" data-tab="memories">记忆</div>
        <div class="admin-tab" data-tab="skills">技能</div>
        <div class="admin-tab" data-tab="config">配置</div>
    </div>

    <div class="admin-content">
        <div id="tab-sessions" class="tab-panel active"><p style="color:var(--dim)">Loading...</p></div>
        <div id="tab-memories" class="tab-panel"><p style="color:var(--dim)">Loading...</p></div>
        <div id="tab-skills" class="tab-panel"><p style="color:var(--dim)">Loading...</p></div>
        <div id="tab-config" class="tab-panel"><p style="color:var(--dim)">Loading...</p></div>
    </div>

    <div id="toast"></div>

    <script src="/static/app.js"></script>
    <script>initAdmin();</script>
</body>
</html>
```

**Step 3: Commit**

```bash
git add src/mini_claude/web/templates/index.html src/mini_claude/web/templates/admin.html
git commit -m "feat: add HTML pages for chat and admin panel"
```

---

### Task 7: 集成 — 更新 __main__.py 支持 --web 启动

**Files:**
- Modify: `src/mini_claude/__main__.py`

**Step 1: 在 parse_args() 中添加 --web 和 --port 参数**

在 `parse_args()` 函数中，在 `--help` 参数之前添加：

```python
parser.add_argument("--web", action="store_true", help="Start web interface (FastAPI)")
parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")
```

**Step 2: 在 main() 中添加 web 启动逻辑**

在 `main()` 函数中，解析完 args 后、创建 Agent 前，检查 `--web` 参数。在 prompt 为空且 `--web` 为 true 时启动 web 服务器：

```python
    prompt = " ".join(args.prompt) if args.prompt else None

    # ─── Web 模式 ──────────────────────────────────────
    if args.web and not prompt:
        import uvicorn
        from .web import create_app
        app = create_app(agent)
        print_info(f"Starting web server at http://localhost:{args.port}")
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
        return

    if prompt:
        # One-shot mode (existing code)
        ...
```

注意：`app = create_app(agent)` 必须在创建 Agent 之后、`if prompt:` 之前。

实际上，更好的做法是在 web 模式下也允许传入 prompt：

```python
    # ─── Web 模式 ──────────────────────────────────────
    if args.web:
        import uvicorn
        from .web import create_app
        app = create_app(agent)
        print_info(f"Starting web server at http://localhost:{args.port}")
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
        return
```

这段放在 `if prompt:` 之前，这样 `--web` 模式下直接启动服务器，忽略 prompt（即使有也忽略）。

**Step 3: 在 __init__.py 中更新 app factory 以支持静态文件服务**

更新 `create_app()` 函数，添加 `/static/` 路由来 serve CSS/JS 文件：

```python
def create_app(agent) -> FastAPI:
    app = FastAPI(title="Mini Claude Code Web")
    app.state.agent = agent

    from .api import router
    app.include_router(router, prefix="/api")

    # Serve static files (CSS, JS)
    @app.get("/static/{filename}")
    async def static_file(filename: str):
        from fastapi.responses import FileResponse
        filepath = _TEMPLATES_DIR / filename
        if not filepath.exists():
            from fastapi import HTTPException
            raise HTTPException(404)
        # Determine media type
        if filename.endswith(".css"):
            return FileResponse(filepath, media_type="text/css")
        elif filename.endswith(".js"):
            return FileResponse(filepath, media_type="application/javascript")
        return FileResponse(filepath)

    @app.get("/")
    async def index():
        from fastapi.responses import HTMLResponse
        content = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(content)

    @app.get("/admin")
    async def admin():
        from fastapi.responses import HTMLResponse
        content = (_TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8")
        return HTMLResponse(content)

    return app
```

**Step 4: Commit**

```bash
git add src/mini_claude/__main__.py src/mini_claude/web/__init__.py
git commit -m "feat: integrate --web flag to launch FastAPI server"
```

---

### Task 8: 验证 — 端到端测试

**Step 1: 安装项目并启动 web 服务器**

```bash
cd src && pip install -e .
mini-claude-py --web --port 8000
```

Expected: 服务器在 `http://localhost:8000` 启动。

**Step 2: 测试页面加载**

在浏览器打开 `http://localhost:8000`。
Expected: 聊天页面加载，显示输入框和快捷命令。

在浏览器打开 `http://localhost:8000/admin`。
Expected: 管理面板加载，4 个 Tab 显示。

**Step 3: 测试 API 端点**

```bash
# 测试状态检查
curl http://localhost:8000/api/status

# 测试会话列表
curl http://localhost:8000/api/sessions

# 测试技能列表
curl http://localhost:8000/api/skills

# 测试记忆列表
curl http://localhost:8000/api/memories

# 测试费用
curl http://localhost:8000/api/cost
```

Expected: 所有端点返回有效的 JSON。

**Step 4: 测试聊天 SSE**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello, say hi back"}'
```

Expected: SSE 流式返回，包含 text 事件和最终的 done 事件。

**Step 5: Commit 验证结果（如有微调）**

```bash
git add -A
git commit -m "chore: final tweaks after e2e verification"
```

---

### Task 9: 更新 CLAUDE.md 文档

**Files:**
- Modify: `CLAUDE.md`

**Step 1: 在架构部分添加 web 模块说明**

在 `CLAUDE.md` 的架构部分，在 `ui.py` 说明之后添加：

```
web/__init__.py →  FastAPI app 工厂、静态文件 serve
web/api.py      →  14 个 API 端点：聊天 SSE、会话/记忆/技能 CRUD、配置等
web/models.py   →  Pydantic 请求/响应模型
web/templates/  →  index.html（聊天页面）、admin.html（管理面板）、style.css、app.js
```

**Step 2: 在常用命令部分添加 web 启动命令**

```bash
# Web 模式
mini-claude-py --web              # 启动浏览器界面（localhost:8000）
mini-claude-py --web --port 3000  # 自定义端口
```

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document web module in CLAUDE.md"
```

---

## 实现顺序总结

1. 添加依赖（pyproject.toml）
2. 创建 web 模块骨架（`__init__.py`、`models.py`）
3. 创建 API 路由 — 聊天 SSE（`api.py` 前半）
4. 完善 API 路由 — 管理端点（`api.py` 后半）
5. 创建前端 CSS + JS（`templates/style.css`、`app.js`）
6. 创建前端 HTML（`templates/index.html`、`admin.html`）
7. 集成 `__main__.py` `--web` 启动
8. 端到端验证
9. 更新 CLAUDE.md 文档

每个任务独立可提交，2-5 分钟完成。
