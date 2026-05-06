"""API routes for the Mini Claude Code web frontend."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .models import (
    ChatRequest, ConfirmRequest, MemoryCreate,
    SessionItem, MemoryItem, SkillItem, AgentStatus, CostInfo,
)

router = APIRouter()

# In-flight confirmations: Agent awaits these when user approval is needed
_pending_confirms: dict[str, asyncio.Future] = {}


def _get_agent(request: Request):
    return request.app.state.agent


def _sse_event(event_type: str, data: dict | str | None = None) -> str:
    """Build an SSE event string."""
    payload = json.dumps(data, ensure_ascii=False) if data is not None else ""
    return f"event: {event_type}\ndata: {payload}\n\n"


# ─── POST /api/chat ─────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Main chat endpoint. Returns an SSE stream of agent responses."""
    agent = _get_agent(request)

    if agent.is_processing:
        async def error_stream():
            yield _sse_event("error", "Agent is busy")
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def event_stream():
        _queue: list[tuple[str, any]] = []

        # 1) Override _emit_text to push SSE text events
        # Detect thinking blocks: agent emits "[thinking]\n " prefix and "\n" suffix
        original_emit = agent._emit_text
        _thinking = False

        def _web_emit(text: str):
            nonlocal _thinking
            if '[thinking]' in text:
                _thinking = True
                _queue.append(("thinking_start", None))
                clean = text.replace('[thinking]\n ', '').replace('[thinking]\n', '')
                if clean.strip():
                    _queue.append(("thinking", clean))
                return
            if _thinking and text == '\n':
                _thinking = False
                _queue.append(("thinking_end", None))
                return
            if _thinking:
                _queue.append(("thinking", text))
            else:
                _queue.append(("text", text))

        agent._emit_text = _web_emit

        # 2) Override confirm_fn to await web confirmation
        original_confirm = agent.confirm_fn

        async def _web_confirm(message: str) -> bool:
            cid = uuid.uuid4().hex[:8]
            _queue.append(("confirm", {"id": cid, "command": message}))
            future: asyncio.Future = asyncio.get_event_loop().create_future()
            _pending_confirms[cid] = future
            try:
                result = await asyncio.wait_for(future, timeout=300)
                return result
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return False
            finally:
                _pending_confirms.pop(cid, None)

        agent.confirm_fn = _web_confirm

        # 3) Monkey-patch UI functions to push SSE events
        import mini_claude.ui as ui_mod

        _orig_tool_call = ui_mod.print_tool_call
        _orig_tool_result = ui_mod.print_tool_result
        _orig_cost = ui_mod.print_cost
        _orig_info = ui_mod.print_info

        def _patched_tool_call(name: str, inp: dict):
            _queue.append(("tool", {"name": name, "input": inp}))

        def _patched_tool_result(name: str, result: str):
            max_len = 500
            display = result[:max_len] + f"\n... ({len(result)} chars)" if len(result) > max_len else result
            # Emit a separate diff event for file modifications
            if name in ("edit_file", "write_file") and not result.startswith("Error"):
                lines = result.split("\n")
                _queue.append(("diff", {"tool": name, "file": lines[0] if lines else "", "lines": lines[1:41]}))
            else:
                _queue.append(("result", {"tool": name, "content": display}))

        def _patched_cost(input_tokens: int, output_tokens: int):
            _queue.append(("cost", {"input": input_tokens, "output": output_tokens}))

        def _patched_info(text: str):
            # Some info messages indicate significant events
            _queue.append(("info", text))

        ui_mod.print_tool_call = _patched_tool_call
        ui_mod.print_tool_result = _patched_tool_result
        ui_mod.print_cost = _patched_cost
        ui_mod.print_info = _patched_info

        try:
            chat_task = asyncio.create_task(agent.chat(req.message))

            while not chat_task.done() or _queue:
                while _queue:
                    evt_type, evt_data = _queue.pop(0)
                    yield _sse_event(evt_type, evt_data)

                if chat_task.done():
                    while _queue:
                        evt_type, evt_data = _queue.pop(0)
                        yield _sse_event(evt_type, evt_data)
                    break

                await asyncio.sleep(0.05)

            if chat_task.exception():
                yield _sse_event("error", str(chat_task.exception()))
            else:
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

        finally:
            agent._emit_text = original_emit
            agent.confirm_fn = original_confirm
            ui_mod.print_tool_call = _orig_tool_call
            ui_mod.print_tool_result = _orig_tool_result
            ui_mod.print_cost = _orig_cost
            ui_mod.print_info = _orig_info

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ─── POST /api/confirm ──────────────────────────────────────

@router.post("/confirm")
async def confirm(req: ConfirmRequest):
    future = _pending_confirms.get(req.id)
    if future and not future.done():
        future.set_result(req.approved)
        return {"ok": True}
    return {"ok": False, "error": "Unknown or expired confirmation id"}


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
    return CostInfo(
        input_tokens=tokens["input"],
        output_tokens=tokens["output"],
        estimated_cost_usd=round(agent._get_current_cost_usd(), 4),
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


@router.post("/abort")
async def abort_agent(request: Request):
    agent = _get_agent(request)
    agent.abort()
    return {"ok": True}


@router.post("/shutdown")
async def shutdown():
    """Shut down the entire server immediately."""
    import os as _os

    def _do_exit():
        _os._exit(0)

    import asyncio as _asyncio
    _asyncio.get_running_loop().call_later(0.3, _do_exit)
    return {"ok": True, "message": "Server shutting down..."}


# ─── Sessions ────────────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionItem])
async def list_sessions():
    from pathlib import Path
    from ..session import list_sessions as _list_sessions, SESSION_DIR
    sessions = _list_sessions()
    result = []
    for s in sessions:
        sid = s.get("id", "")
        valid = True
        try:
            valid = (Path(SESSION_DIR) / f"{sid}.json").exists()
        except Exception:
            valid = False
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
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        return {"ok": False, "error": "Invalid session ID"}
    from ..session import load_session
    agent = _get_agent(request)
    data = load_session(session_id)
    if not data:
        return {"ok": False, "error": "Session not found"}
    agent.restore_session(data)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Return extracted user/assistant messages from a session file."""
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        return {"ok": False, "error": "Invalid session ID"}
    from ..session import load_session
    data = load_session(session_id)
    if not data:
        return {"ok": False, "error": "Session not found"}

    raw = data.get("anthropicMessages") or data.get("openaiMessages") or []
    messages = []
    for msg in raw:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Anthropic content blocks: extract text
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        texts.append(f"[tool: {block.get('name', '')}]")
                    elif block.get("type") == "tool_result":
                        c = block.get("content", "")
                        if isinstance(c, str) and len(c) > 200:
                            c = c[:200] + "..."
                        texts.append(f"[result: {c}]")
            content = "\n".join(texts) if texts else ""

        if role in ("user", "assistant") and content:
            # Skip system messages and empty content
            messages.append({"role": role, "content": str(content)})

    return {"ok": True, "messages": messages}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        return {"ok": False, "error": "Invalid session ID"}
    from pathlib import Path
    from ..session import SESSION_DIR
    path = Path(SESSION_DIR) / f"{session_id}.json"
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
    path = (get_memory_dir() / filename).resolve()
    if not path.is_relative_to(get_memory_dir()):
        return {"ok": False, "error": "Invalid filename"}
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
    from ..memory import get_memory_dir
    path = (get_memory_dir() / filename).resolve()
    if not path.is_relative_to(get_memory_dir()):
        return {"ok": False, "error": "Invalid filename"}
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
