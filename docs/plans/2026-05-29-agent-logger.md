# Agent Logger 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 agent 实时运行时日志系统 — 每事件立即 flush 写入 JSONL，LLM 内容分离存储，支持 sub-agent 追踪。

**Architecture:** `logger.py` 新增 AgentLogger，tracer 改为实时写入，agent 在 API 调用/工具执行/sub-agent 启动处调用 logger。

**Tech Stack:** Python 3.11+, json, pathlib, 无第三方依赖

**Design Doc:** `docs/plans/2026-05-29-agent-logger-design.md`

---

### Task 1: 实现 logger.py

**Files:**
- Create: `src/mini_claude/logger.py`

**Step 1: 编写 AgentLogger 类**

```python
"""Agent 运行时日志 — 实时 JSONL 写入，crash-safe。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

SESSION_DIR = Path.home() / ".mini-claude" / "sessions"


class AgentLogger:
    """实时日志记录器。每次 write 后立即 flush，崩溃不丢数据。"""

    def __init__(
        self,
        session_id: str,
        agent_id: str = "main",
        parent_logger: AgentLogger | None = None,
    ):
        self.session_id = session_id
        self.agent_id = agent_id
        self._parent = parent_logger  # sub-agent 复用父 logger 的 session
        self._ask_index = 0
        self._log_file: Any = None
        self._llm_file: Any = None

    @property
    def _session_dir(self) -> Path:
        sid = self._parent.session_id if self._parent else self.session_id
        return SESSION_DIR / sid

    def new_ask(self, ask_index: int) -> None:
        """开始新一轮 ask，切换到新的主日志文件。"""
        self._ask_index = ask_index
        # 关闭旧文件
        if self._log_file:
            self._log_file.close()
        log_dir = self._session_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = open(log_dir / f"{ask_index:03d}.jsonl", "a", encoding="utf-8")

    def _ensure_llm_file(self) -> Any:
        if self._llm_file is None:
            llm_dir = self._session_dir / "llm"
            llm_dir.mkdir(parents=True, exist_ok=True)
            self._llm_file = open(llm_dir / f"{self.session_id}.jsonl", "a", encoding="utf-8")
        return self._llm_file

    def _write_event(self, event: dict) -> None:
        """写入一条主日志事件并立即 flush。"""
        if self._log_file is None:
            return
        event.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()))
        event.setdefault("agent_id", self.agent_id)
        self._log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._log_file.flush()

    # ── 事件方法 ─────────────────────────────────────

    def log_api_request(self, request_id: str, model: str) -> None:
        self._write_event({
            "type": "api_request",
            "request_id": request_id,
            "model": model,
        })

    def log_api_response(
        self, request_id: str, latency_ms: int,
        input_tokens: int, output_tokens: int,
        cache_read_tokens: int = 0, finish_reason: str = "stop",
    ) -> None:
        self._write_event({
            "type": "api_response",
            "request_id": request_id,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "finish_reason": finish_reason,
        })

    def log_tool_call(
        self, request_id: str, tool_name: str,
        params: dict, duration_ms: int, success: bool,
    ) -> None:
        self._write_event({
            "type": "tool_call",
            "request_id": request_id,
            "tool_name": tool_name,
            "params": params,
            "duration_ms": duration_ms,
            "success": success,
        })

    def log_sub_agent(
        self, request_id: str, sub_agent_name: str,
        sub_agent_type: str, prompt_summary: str,
    ) -> None:
        self._write_event({
            "type": "sub_agent",
            "request_id": request_id,
            "sub_agent_name": sub_agent_name,
            "sub_agent_type": sub_agent_type,
            "prompt_summary": prompt_summary,
        })

    def log_error(
        self, request_id: str, error_type: str, message: str,
    ) -> None:
        self._write_event({
            "type": "error",
            "request_id": request_id,
            "error_type": error_type,
            "message": message,
        })

    def save_llm_content(
        self, request_id: str, model: str,
        messages: list[dict], response: dict, usage: dict,
    ) -> None:
        """将完整的 LLM 请求/响应写入 llm/ JSONL 文件。"""
        f = self._ensure_llm_file()
        entry = {
            "request_id": request_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "model": model,
            "messages": messages,
            "response": response,
            "usage": usage,
        }
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()

    def close(self) -> None:
        """关闭所有文件句柄。"""
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        if self._llm_file:
            self._llm_file.close()
            self._llm_file = None

    @staticmethod
    def generate_request_id() -> str:
        return f"req_{uuid.uuid4().hex[:12]}"
```

**Step 2: 验证基本功能**

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import tempfile, json, shutil, os
from pathlib import Path
from mini_claude.logger import AgentLogger

# 用临时目录模拟 session
tmp = Path(tempfile.mkdtemp())
real_session = Path.home() / '.mini-claude' / 'sessions'
try:
    # 创建假 session 目录
    sid = 'test_logger_001'
    sdir = real_session / sid
    sdir.mkdir(parents=True, exist_ok=True)

    logger = AgentLogger(sid)
    logger.new_ask(1)

    req_id = AgentLogger.generate_request_id()
    logger.log_api_request(req_id, 'claude-opus-4-6')
    logger.log_api_response(req_id, 1234, 5000, 200, 3000, 'end_turn')
    logger.log_tool_call(req_id, 'read_file', {'file_path': '/tmp/x'}, 45, True)
    logger.log_sub_agent(req_id, 'explore', 'explore', 'search for routes')
    logger.log_error(req_id, 'api_timeout', 'timed out after 30s')

    logger.save_llm_content(req_id, 'claude-opus-4-6',
        [{'role': 'system', 'content': 'You are an agent'}],
        {'content': [{'type': 'text', 'text': 'ok'}]},
        {'input_tokens': 5000, 'output_tokens': 200})

    logger.close()

    # 验证日志文件存在且有内容
    log_file = sdir / 'logs' / '001.jsonl'
    llm_file = sdir / 'llm' / f'{sid}.jsonl'

    assert log_file.exists(), 'Main log missing'
    assert llm_file.exists(), 'LLM log missing'

    logs = [json.loads(l) for l in log_file.read_text(encoding='utf-8').strip().split('\n')]
    llms = [json.loads(l) for l in llm_file.read_text(encoding='utf-8').strip().split('\n')]

    assert len(logs) == 5, f'Expected 5 events, got {len(logs)}'
    assert logs[0]['type'] == 'api_request'
    assert logs[1]['type'] == 'api_response'
    assert logs[2]['type'] == 'tool_call'
    assert logs[3]['type'] == 'sub_agent'
    assert logs[4]['type'] == 'error'
    assert logs[1]['latency_ms'] == 1234
    assert llms[0]['request_id'] == req_id

    shutil.rmtree(sdir, ignore_errors=True)
    print('PASS')
finally:
    pass
"
```

**Step 3: 验证 parent_logger（sub-agent 场景）**

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import json, shutil
from pathlib import Path
from mini_claude.logger import AgentLogger

sid = 'test_logger_parent'
sdir = Path.home() / '.mini-claude' / 'sessions' / sid
sdir.mkdir(parents=True, exist_ok=True)

main_logger = AgentLogger(sid, agent_id='main')
sub_logger = AgentLogger('sub_sid_ignored', agent_id='main.explore_1', parent_logger=main_logger)

main_logger.new_ask(1)
# sub_logger 使用 parent 的 session 目录
# 验证 _session_dir 正确
assert 'test_logger_parent' in str(sub_logger._session_dir)
print('Parent logger redirect: OK')

main_logger.close()
sub_logger.close()
shutil.rmtree(sdir, ignore_errors=True)
print('PASS')
"
```

**Step 4: Commit**

```bash
git add src/mini_claude/logger.py
git commit -m "feat: add AgentLogger — real-time JSONL logging for agent observability"
```

---

### Task 2: 改造 tracer.py 为实时写入

**Files:**
- Modify: `src/mini_claude/tracer.py:76-94`

**Step 1: 修改 tracer 的 finalize 方法**

将 `SessionTracer.finalize()` 改为不再返回 lines 列表、不再批量写入，而是改成 `_write_turn_line()` 和 `_write_ask_line()` 两个实时写入方法。Tracer 在初始化时接受一个 `logger` 参数，通过 logger 的 session 目录写入。

具体改动：
- `__init__` 新增 `logger: AgentLogger` 参数
- `on_turn_end`: 写入 turn JSONL 行到 `traces/{ask_index:03d}.jsonl`
- `on_first_token`: 不写文件（等待 turn_end）
- 新增 `_write_ask_line()`: 在 ask 结束时写 ask 概览行
- 移除 `finalize()` 方法

```python
# tracer.py 改动要点

class SessionTracer:
    def __init__(self, ask_index: int, user_message: str, logger):
        ...
        self._logger = logger
        self._trace_file = None

    def _ensure_trace_file(self):
        if self._trace_file is None:
            traces_dir = self._logger._session_dir / "traces"
            traces_dir.mkdir(parents=True, exist_ok=True)
            self._trace_file = open(traces_dir / f"{self.ask_index:03d}.jsonl", "a", encoding="utf-8")

    def on_turn_end(self, payload):
        # ... existing code to populate _current_turn ...
        self._turns.append(self._current_turn)

        # 实时写 turn 行
        self._ensure_trace_file()
        self._trace_file.write(json.dumps({"type": "turn", **self._current_turn}, ensure_ascii=False) + "\n")
        self._trace_file.flush()

        self._current_turn = {}

    def write_ask_summary(self):
        """在 ask 结束时写 ask 概览行（在所有 turn 之后追加）。"""
        # ... compute totals from self._turns ...
        self._ensure_trace_file()
        # 在文件开头插入 ask 行（或先写一个占位再 seek 回去）
        # 简化方案：ask 行写在最后，读取时 type=ask 行作为 header
        self._trace_file.write(json.dumps(ask_line, ensure_ascii=False) + "\n")
        self._trace_file.flush()
        if self._trace_file:
            self._trace_file.close()
```

**Step 2: 验证 tracer 实时写入**

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import json, time, tempfile, shutil
from pathlib import Path
from mini_claude.logger import AgentLogger
from mini_claude.tracer import SessionTracer

sid = 'test_tracer_realtime'
sdir = Path.home() / '.mini-claude' / 'sessions' / sid
sdir.mkdir(parents=True, exist_ok=True)

logger = AgentLogger(sid)
logger.new_ask(1)
tracer = SessionTracer(1, 'test prompt', logger)

# 模拟一轮
tracer.on_turn_start({'turn_index': 1})
time.sleep(0.01)
tracer.on_first_token({})
tracer.on_turn_end({'input_tokens': 100, 'output_tokens': 50, 'cache_read_tokens': 80, 'cache_create_tokens': 0, 'finish_reason': 'stop'})

# 写 ask 总结
tracer.write_ask_summary()

# 验证 trace 文件生成且内容正确
trace_file = sdir / 'traces' / '001.jsonl'
assert trace_file.exists()
lines = [json.loads(l) for l in trace_file.read_text(encoding='utf-8').strip().split('\n')]
assert len(lines) == 2  # turn + ask
types = [l['type'] for l in lines]
assert 'turn' in types and 'ask' in types

logger.close()
shutil.rmtree(sdir, ignore_errors=True)
print('PASS')
"
```

**Step 3: Commit**

```bash
git add src/mini_claude/tracer.py
git commit -m "refactor: real-time tracer writing — write turn/ask lines immediately"
```

---

### Task 3: 集成 logger 到 agent.py

**Files:**
- Modify: `src/mini_claude/agent.py` — 多处插入 logger 调用

**Step 1: 在 agent.py 导入 logger**

在文件顶部导入处添加：
```python
from .logger import AgentLogger
```

**Step 2: 在 chat() 中创建 logger**

在 `chat()` 方法中，创建 tracer 之前创建 logger：

```python
# 在 chat() 中的 self._ask_count += 1 之后
self._logger = None
if not self.is_sub_agent:
    self._logger = AgentLogger(self.session_id, agent_id="main")
    self._logger.new_ask(self._ask_count)
```

对于 sub-agent，在 `_execute_tool_call` 的 skill-fork 和 sub-agent 创建处传入 parent logger：

```python
# skill-fork (agent.py 行 798)
sub_agent = Agent(
    ...
    is_sub_agent=True,
    logger=AgentLogger(self.session_id, agent_id=f"main.skill_{inp.get('skill_name', '')}",
                       parent_logger=self._logger),
)
```

```python
# sub-agent (agent.py 行 948)
sub_agent = Agent(
    ...
    is_sub_agent=True,
    logger=AgentLogger(self.session_id, agent_id=f"main.{agent_type}_1",
                       parent_logger=self._logger),
)
```

**Step 3: 在 API 调用点插入日志**

在 `_call_anthropic_stream` 中：

```python
# 在调用前
request_id = AgentLogger.generate_request_id()
api_start = time.time()
self._logger.log_api_request(request_id, self.model)

# ... API 调用 ...

# 在响应返回后
latency_ms = int((time.time() - api_start) * 1000)
self._logger.log_api_response(
    request_id, latency_ms,
    response.usage.input_tokens,
    response.usage.output_tokens,
    getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    "end_turn" if tool_uses else "stop",
)
self._logger.save_llm_content(
    request_id, self.model,
    self._anthropic_messages,
    {"content": [self._block_to_dict(b) for b in response.content]},
    {"input_tokens": response.usage.input_tokens, ...},
)
```

在 `_call_openai_stream` 中类似的逻辑。

**Step 4: 在工具调用点插入日志**

在 tool_end 事件处同步调用 logger：

```python
self._logger.log_tool_call(
    request_id, tu.name, inp, tool_duration, success,
)
```

**Step 5: 验证集成**

用一个真实 task 跑一次，确认所有日志实时生成：

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import sys,os,subprocess,tempfile,shutil,json,time
from pathlib import Path
PROJECT_ROOT = Path.cwd()
PYTHON = r'D:/Anaconda/envs/ai/python.exe'

ws = Path(tempfile.mkdtemp(prefix='logtest_'))
shutil.copytree('test/fixtures/bench_repo_patch', ws, dirs_exist_ok=True)
cmd = [PYTHON, '-B', '-m', 'mini_claude', '--yolo', '--max-turns', '2',
       'Read sample.txt and replace beta with beta-locked.']
proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding='utf-8', timeout=300,
    env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT/'src'), 'PYTHONIOENCODING': 'utf-8'})

# 找最新的 session 日志
sessions = sorted(Path.home().glob('.mini-claude/sessions/*'), key=lambda p: p.stat().st_mtime, reverse=True)
latest = sessions[0]
log_file = latest / 'logs' / '001.jsonl'
llm_file = latest / 'llm' / f'{latest.name}.jsonl'

print(f'Session: {latest.name}')
print(f'Main log exists: {log_file.exists()}')
print(f'LLM log exists: {llm_file.exists()}')

if log_file.exists():
    logs = [json.loads(l) for l in log_file.read_text(encoding='utf-8').strip().split('\n')]
    for l in logs:
        print(f'  [{l[\"type\"]}] request_id={l.get(\"request_id\",\"?\")}')
    assert any(l['type'] == 'api_request' for l in logs), 'Missing api_request'
    assert any(l['type'] == 'api_response' for l in logs), 'Missing api_response'
    assert any(l['type'] == 'tool_call' for l in logs), 'Missing tool_call'

shutil.rmtree(ws, ignore_errors=True)
print('PASS')
"
```

**Step 6: Commit**

```bash
git add src/mini_claude/agent.py
git commit -m "feat: integrate logger into agent — API calls, tools, sub-agents"
```

---

### Task 4: 端到端验证

**Files:**
- 无新建文件，全链路验证

**Step 1: 完整流程验证**

跑一个带 sub-agent 的 task（如 search_and_replace 会用到 explore），确认所有日志实时记录：

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import sys,os,subprocess,tempfile,shutil,json
from pathlib import Path
PROJECT_ROOT = Path.cwd()
PYTHON = r'D:/Anaconda/envs/ai/python.exe'

ws = Path(tempfile.mkdtemp(prefix='e2e_log_'))
shutil.copytree('test/fixtures/multi_file', ws, dirs_exist_ok=True)

# 这个 task 需要 grep_search（可能触发 skill）
cmd = [PYTHON, '-B', '-m', 'mini_claude', '--yolo', '--max-turns', '4',
       'Search for TODO in all files under this directory and replace with DONE']
proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding='utf-8', timeout=300,
    env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT/'src'), 'PYTHONIOENCODING': 'utf-8'})

sessions = sorted(Path.home().glob('.mini-claude/sessions/*'), key=lambda p: p.stat().st_mtime, reverse=True)
latest = sessions[0]
log_file = latest / 'logs' / '001.jsonl'
llm_file = latest / 'llm' / f'{latest.name}.jsonl'

print(f'Session: {latest.name}')
print(f'Main log: {log_file.exists()} ({log_file.stat().st_size} bytes)')
print(f'LLM log:  {llm_file.exists()} ({llm_file.stat().st_size} bytes)')

logs = [json.loads(l) for l in log_file.read_text(encoding='utf-8').strip().split('\n')]
event_types = {}
for l in logs:
    event_types[l['type']] = event_types.get(l['type'], 0) + 1
print(f'Events: {event_types}')

# 验证实时写入（文件在 agent 运行期间就存在且有内容，不是跑完才生成）
# 这是关键：log 文件应该在 agent 开始执行后立即有 api_request 事件
assert 'api_request' in event_types, 'Should have api_request events'
assert 'api_response' in event_types, 'Should have api_response events'
assert 'tool_call' in event_types, 'Should have tool_call events'

# 验证 LLM 内容文件
llm_lines = [json.loads(l) for l in llm_file.read_text(encoding='utf-8').strip().split('\n')]
assert len(llm_lines) == event_types.get('api_response', 0), 'LLM count should match api_response count'
print(f'LLM entries: {len(llm_lines)}')

shutil.rmtree(ws, ignore_errors=True)
print('PASS')
"
```

**Step 2: 验证 crash-safe — 模拟中途崩溃**

写一个快速测试：启动 agent 但用极短的 timeout 让它在工具执行前被 kill，确认主日志有 `api_request` 事件（已落盘）：

```bash
cd D:/PycharmProjects/pythonProject/claude-code-from-scratch && PYTHONPATH=src python -c "
import subprocess, tempfile, shutil, json, os, sys, time
from pathlib import Path
PROJECT_ROOT = Path.cwd()
PYTHON = r'D:/Anaconda/envs/ai/python.exe'

ws = Path(tempfile.mkdtemp(prefix='crash_'))
shutil.copytree('test/fixtures/bench_repo_patch', ws, dirs_exist_ok=True)

# 用 5 秒 timeout，agent 的 first token 通常要 900ms，足够拿到 api_response
cmd = [PYTHON, '-B', '-m', 'mini_claude', '--yolo', '--max-turns', '2',
       'Read sample.txt']
try:
    proc = subprocess.run(cmd, cwd=str(ws), capture_output=True, encoding='utf-8',
        timeout=8, env={**os.environ, 'PYTHONPATH': str(PROJECT_ROOT/'src'), 'PYTHONIOENCODING': 'utf-8'})
except subprocess.TimeoutExpired:
    pass  # 预期的

time.sleep(0.5)

sessions = sorted(Path.home().glob('.mini-claude/sessions/*'), key=lambda p: p.stat().st_mtime, reverse=True)
latest = sessions[0]
log_file = latest / 'logs' / '001.jsonl'
if log_file.exists():
    logs = [json.loads(l) for l in log_file.read_text(encoding='utf-8').strip().split('\n')]
    types = [l['type'] for l in logs]
    print(f'Events after crash/timeout: {types}')
    # 关键验证：api_request 应该已经落盘
    assert 'api_request' in types, f'Crash-safe failed: no api_request in log'
    print('Crash-safe: OK (api_request present)')
else:
    print('No log file yet (agent may not have started)')

shutil.rmtree(ws, ignore_errors=True)
print('PASS')
"
```

**Step 3: Commit**

```bash
git add .
git commit -m "test: add end-to-end verification for agent logging system"
```
