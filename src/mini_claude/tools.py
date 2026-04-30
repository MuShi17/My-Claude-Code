"""工具定义与执行 — 13个工具，5种权限模式。
复现 Claude Code 的工具系统：read_file, write_file, edit_file, list_files,
grep_search, run_shell, skill, enter/exit_plan_mode, agent, tool_search, web_fetch。

工具执行流程：
  1. 模型返回 tool_use 块 → 解析为 {name, input} 字典
  2. check_permission() 根据权限模式 + 规则决定 allow/deny/confirm
  3. execute_tool() 执行实际逻辑，返回字符串结果
  4. 结果截断后注入回对话上下文
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .memory import get_memory_dir
from .frontmatter import parse_frontmatter

# ─── 权限模式 ──────────────────────────────────────────────
# 5种模式控制工具执行的安全级别，从完全开放到只读计划模式

PermissionMode = str  # "default" | "plan" | "acceptEdits" | "bypassPermissions" | "dontAsk"

# 只读工具：在任何模式下都不需要用户确认
READ_TOOLS = {"read_file", "list_files", "grep_search", "web_fetch"}
# 编辑工具：会修改文件系统的工具
EDIT_TOOLS = {"write_file", "edit_file"}

# 可并发执行的工具：无副作用，可以在同一轮中并行调用以加速响应
CONCURRENCY_SAFE_TOOLS = {"read_file", "list_files", "grep_search", "web_fetch"}

IS_WIN = sys.platform == "win32"

# ─── 类型别名 ──────────────────────────────────────────────

ToolDef = dict  # Anthropic 兼容的 tool schema 字典

# ─── 工具定义 ───────────────────────────────────────────────
# 每个工具遵循 Anthropic tool use 格式：name、description、input_schema
# deferred=True 的工具默认不发送给模型，需通过 tool_search 动态激活

tool_definitions: list[ToolDef] = [
    # ── 文件读取 ──
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the file content with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to read"},
            },
            "required": ["file_path"],
        },
    },
    # ── 文件写入 ──
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"},
            },
            "required": ["file_path", "content"],
        },
    },
    # ── 文件编辑 ──
    {
        "name": "edit_file",
        "description": "Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "The path to the file to edit"},
                "old_string": {"type": "string", "description": "The exact string to find and replace"},
                "new_string": {"type": "string", "description": "The string to replace it with"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    # ── 文件列表（glob 匹配）──
    {
        "name": "list_files",
        "description": "List files matching a glob pattern. Returns matching file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": 'Glob pattern to match files (e.g., "**/*.ts", "src/**/*")'},
                "path": {"type": "string", "description": "Base directory to search from. Defaults to current directory."},
            },
            "required": ["pattern"],
        },
    },
    # ── 内容搜索（正则匹配）──
    {
        "name": "grep_search",
        "description": "Search for a pattern in files. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "The regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in. Defaults to current directory."},
                "include": {"type": "string", "description": 'File glob pattern to include (e.g., "*.ts", "*.py")'},
            },
            "required": ["pattern"],
        },
    },
    # ── Shell 命令执行 ──
    {
        "name": "run_shell",
        "description": "Execute a shell command and return its output. Use this for running tests, installing packages, git operations, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "number", "description": "Timeout in milliseconds (default: 30000)"},
            },
            "required": ["command"],
        },
    },
    # ── 技能调用 ──
    {
        "name": "skill",
        "description": "Invoke a registered skill by name. Skills are prompt templates loaded from .claude/skills/. Returns the skill's resolved prompt to follow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "The name of the skill to invoke"},
                "args": {"type": "string", "description": "Optional arguments to pass to the skill"},
            },
            "required": ["skill_name"],
        },
    },
    # ── 网页抓取 ──
    {
        "name": "web_fetch",
        "description": "Fetch a URL and return its content as text. For HTML pages, tags are stripped to return readable text. For JSON/text responses, content is returned directly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "max_length": {"type": "number", "description": "Maximum content length in characters (default 50000)"},
            },
            "required": ["url"],
        },
    },
    # ── 计划模式进入（延迟激活）──
    {
        "name": "enter_plan_mode",
        "description": "Enter plan mode to switch to a read-only planning phase. In plan mode, you can only read files and write to the plan file.",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    # ── 计划模式退出（延迟激活）──
    {
        "name": "exit_plan_mode",
        "description": "Exit plan mode after you have finished writing your plan to the plan file.",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    # ── 子 Agent 调度 ──
    {
        "name": "agent",
        "description": "Launch a sub-agent to handle a task autonomously. Sub-agents have isolated context and return their result. Types: 'explore' (read-only), 'plan' (read-only, structured planning), 'general' (full tools).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short (3-5 word) description of the sub-agent's task"},
                "prompt": {"type": "string", "description": "Detailed task instructions for the sub-agent"},
                "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type. Default: general"},
            },
            "required": ["description", "prompt"],
        },
    },
    # ── 工具搜索（延迟工具加载器）──
    # 根据关键词搜索已定义但尚未激活的延迟工具，返回其完整 schema 并激活
    {
        "name": "tool_search",
        "description": "Search for available tools by name or keyword. Returns full schema definitions for matching deferred tools so you can use them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tool name or search keywords"},
            },
            "required": ["query"],
        },
    },
]

# ─── 延迟工具激活机制 ─────────────────────────────────────
# 延迟工具（deferred=True）默认不发送给模型，减少每次 API 调用的 tool schema 体积。
# 当模型尝试调用 tool_search 查找特定工具时，才动态激活并加入后续轮次。
# 这避免了 enter_plan_mode/exit_plan_mode 等不常用工具占用上下文。

_activated_tools: set[str] = set()


def reset_activated_tools() -> None:
    """重置已激活工具集合，通常在会话重置时调用。"""
    _activated_tools.clear()


def get_active_tool_definitions(all_tools: list[ToolDef] | None = None) -> list[ToolDef]:
    """返回当前活跃的工具定义列表，排除未激活的延迟工具。
    同时剥离 'deferred' 键，避免发送给 API（API 不识别该字段）。"""
    tools = all_tools if all_tools is not None else tool_definitions
    return [
        {k: v for k, v in t.items() if k != "deferred"}
        for t in tools
        if not t.get("deferred") or t["name"] in _activated_tools
    ]


def get_deferred_tool_names(all_tools: list[ToolDef] | None = None) -> list[str]:
    """返回尚未激活的延迟工具名称列表，供 tool_search 匹配使用。"""
    tools = all_tools if all_tools is not None else tool_definitions
    return [t["name"] for t in tools if t.get("deferred") and t["name"] not in _activated_tools]


# ─── 工具执行实现 ─────────────────────────────────────────
# 每个工具对应一个 _<tool_name>() 函数，输入为解析后的参数字典，返回字符串结果
# agent 和 skill 工具在 agent.py 中处理，避免循环导入

def _read_file_with_encoding(file_path: str) -> tuple[str, str]:
    """读取文件内容，尝试不同的编码。"""
    encodings = ['utf-8', 'gbk', 'gb2312']
    content = None
    last_error = None
    for enc in encodings:
        try:
            content = Path(file_path).read_text(encoding=enc)
            break
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except Exception as e:
            return f"Error reading file: {e}"
    return content, last_error


def _read_file(inp: dict) -> str:
    """读取文件内容，返回带行号的文本（格式：行号 | 内容）。"""
    file_path = inp["file_path"]
    # 尝试的编码列表（按优先级）
    content, last_error = _read_file_with_encoding(file_path)
    if content is None:
        return f"Error reading file: cannot decode with any encoding (last error: {last_error})"
    # 添加行号
    lines = content.split("\n")
    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
    return numbered


def _write_file(inp: dict) -> str:
    """写入文件内容。
    - 自动创建父目录
    - 如果写入的是记忆文件，自动更新 MEMORY.md 索引
    - 返回写入后的行数和前30行预览
    """
    try:
        path = Path(inp["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"], encoding="utf-8")
        _auto_update_memory_index(str(path))
        lines = inp["content"].split("\n")
        line_count = len(lines)
        preview = "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(lines[:30]))
        trunc = f"\n  ... ({line_count} lines total)" if line_count > 30 else ""
        return f"Successfully wrote to {inp['file_path']} ({line_count} lines)\n\n{preview}{trunc}"
    except Exception as e:
        return f"Error writing file: {e}"


def _auto_update_memory_index(file_path: str) -> None:
    """写入记忆目录下的 .md 文件后，自动重新生成 MEMORY.md 索引。
    解析每个记忆文件的 YAML frontmatter（name、type、description），
    生成 Markdown 格式的索引链接列表。
    非记忆目录下的文件或 MEMORY.md 本身不会触发重建。
    """
    try:
        mem_dir = str(get_memory_dir())
        if file_path.startswith(mem_dir) and file_path.endswith(".md") and not file_path.endswith("MEMORY.md"):
            mem_path = Path(mem_dir)
            lines = ["# Memory Index", ""]
            for f in sorted(mem_path.glob("*.md")):
                if f.name == "MEMORY.md":
                    continue
                try:
                    raw = f.read_text()
                    # 从 YAML frontmatter 中提取元数据字段
                    name_match = re.search(r"^name:\s*(.+)$", raw, re.MULTILINE)
                    type_match = re.search(r"^type:\s*(.+)$", raw, re.MULTILINE)
                    desc_match = re.search(r"^description:\s*(.+)$", raw, re.MULTILINE)
                    if name_match and type_match:
                        n = name_match.group(1).strip()
                        t = type_match.group(1).strip()
                        d = desc_match.group(1).strip() if desc_match else ""
                        lines.append(f"- **[{n}]({f.name})** ({t}) — {d}")
                except Exception:
                    pass
            (mem_path / "MEMORY.md").write_text("\n".join(lines))
    except Exception:
        pass


# ─── 编辑辅助函数：引号规范化 + diff 生成 ────────────────
# edit_file 的核心挑战是精确匹配：模型可能使用"智能引号"，而文件使用的是直引号
# 因此需要引号规范化来容忍这种差异


def _normalize_quotes(s: str) -> str:
    """将 Unicode 智能引号（弯引号）统一转换为 ASCII 直引号。
    解决 LLM 输出的 'old_string' 与文件实际内容引号不匹配的问题。"""
    s = re.sub("[\u2018\u2019\u2032]", "'", s)   # 各种单引号 → '
    s = re.sub('[\u201c\u201d\u2033]', '"', s)   # 各种双引号 → "
    return s


def _find_actual_string(file_content: str, search_string: str) -> str | None:
    """在文件内容中查找搜索字符串，支持引号规范化容错匹配。
    优先精确匹配；失败后尝试引号规范化匹配，返回文件中的原始字符串（保持原引号风格）。
    """
    if search_string in file_content:
        return search_string
    # 引号规范化后重试匹配
    norm_search = _normalize_quotes(search_string)
    norm_file = _normalize_quotes(file_content)
    idx = norm_file.find(norm_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    return None


def _generate_diff(old_content: str, old_string: str, new_string: str) -> str:
    """生成 unified diff 格式的变更预览。
    计算 old_string 在原文件中的行号，输出类似 git diff 的 +/- 对比。"""
    before_change = old_content.split(old_string)[0]
    line_num = before_change.count("\n") + 1
    old_lines = old_string.split("\n")
    new_lines = new_string.split("\n")

    parts = [f"@@ -{line_num},{len(old_lines)} +{line_num},{len(new_lines)} @@"]
    for l in old_lines:
        parts.append(f"- {l}")
    for l in new_lines:
        parts.append(f"+ {l}")
    return "\n".join(parts)


def _edit_file(inp: dict) -> str:
    """编辑文件：查找 old_string 并替换为 new_string。
    包含安全检查：
    - old_string 必须在文件中存在（支持引号规范化容错）
    - old_string 在文件中必须唯一（防止意外替换多处）
    - 返回 unified diff 格式的变更预览
    """
    try:
        path = Path(inp["file_path"])
        content = path.read_text()

        actual = _find_actual_string(content, inp["old_string"])
        if not actual:
            return f"Error: old_string not found in {inp['file_path']}"

        count = content.count(actual)
        if count > 1:
            return f"Error: old_string found {count} times in {inp['file_path']}. Must be unique."

        new_content = content.replace(actual, inp["new_string"], 1)
        path.write_text(new_content)

        diff = _generate_diff(content, actual, inp["new_string"])
        quote_note = " (matched via quote normalization)" if actual != inp["old_string"] else ""
        return f"Successfully edited {inp['file_path']}{quote_note}\n\n{diff}"
    except Exception as e:
        return f"Error editing file: {e}"


def _list_files(inp: dict) -> str:
    """按 glob 模式列出文件。
    - 自动跳过 node_modules 和 .git 目录
    - 最多返回 200 个匹配结果，超出部分截断并提示
    """
    try:
        base = Path(inp.get("path") or ".")
        pattern = inp["pattern"]
        files = []
        for p in base.glob(pattern):
            if p.is_file():
                rel = str(p.relative_to(base) if base != Path(".") else p)
                # Skip node_modules and .git
                if "node_modules" in rel or ".git" in rel.split(os.sep):
                    continue
                files.append(rel)
                if len(files) >= 200:
                    break
        if not files:
            return "No files found matching the pattern."
        result = "\n".join(files[:200])
        if len(files) > 200:
            result += f"\n... and {len(files) - 200} more"
        return result
    except Exception as e:
        return f"Error listing files: {e}"


def _grep_search(inp: dict) -> str:
    """正则搜索文件内容，双引擎策略：
    - Linux/macOS：优先使用系统 grep（性能更好）
    - Windows 或 grep 不可用时：回退到纯 Python 实现
    最多输出 100 行匹配结果，超出截断提示。
    """
    pattern = inp["pattern"]
    path = inp.get("path") or "."
    include = inp.get("include")

    # Linux/macOS：优先使用系统 grep，利用 native 性能
    if not IS_WIN:
        try:
            args = ["grep", "--line-number", "--color=never", "-r"]
            if include:
                args.append(f"--include={include}")
            args.extend(["--", pattern, path])
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 1:
                return "No matches found."
            if result.returncode == 0:
                lines = [l for l in result.stdout.split("\n") if l]
                output = "\n".join(lines[:100])
                if len(lines) > 100:
                    output += f"\n... and {len(lines) - 100} more matches"
                return output
            # 非 0 也非 1 的退出码（如 grep 报错），回退到 Python 实现
        except Exception:
            pass  # grep 不可用时静默回退

    # 纯 Python 回退（Windows 或系统 grep 不可用）
    return _grep_python(pattern, path, include)


def _grep_python(pattern: str, directory: str, include: str | None) -> str:
    """纯 Python 实现的正则搜索。
    递归遍历目录树，对每个文件逐行匹配，自动跳过隐藏目录和 node_modules。
    最多收集 200 条匹配（输出前 100 行），防止大项目内存爆炸。
    """
    regex = re.compile(pattern)
    include_pattern = include
    matches: list[str] = []

    def walk(d: str) -> None:
        if len(matches) >= 200:
            return
        try:
            entries = os.listdir(d)
        except Exception:
            return
        for name in entries:
            # 跳过隐藏文件和 node_modules（大型目录，不相关）
            if name.startswith(".") or name == "node_modules":
                continue
            full = os.path.join(d, name)
            if os.path.isdir(full):
                walk(full)
                continue
            if include_pattern and not fnmatch.fnmatch(name, include_pattern):
                continue
            try:
                text, _ = _read_file_with_encoding(full)
                for i, line in enumerate(text.split("\n")):
                    if regex.search(line):
                        matches.append(f"{full}:{i+1}:{line}")
                        if len(matches) >= 200:
                            return
            except Exception:
                pass

    walk(directory)
    if not matches:
        return "No matches found."
    output = "\n".join(matches[:100])
    if len(matches) > 100:
        output += f"\n... and {len(matches) - 100} more matches"
    return output


def _run_shell(inp: dict) -> str:
    """执行 shell 命令。
    - 默认超时 30 秒
    - 捕获 stdout 和 stderr
    - 非零退出码时返回详细错误信息（包含 stdout/stderr）
    """
    try:
        timeout_ms = inp.get("timeout", 30000)
        timeout_s = timeout_ms / 1000
        result = subprocess.run(
            inp["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = result.stdout or ""
        if result.returncode != 0:
            stderr = f"\nStderr: {result.stderr}" if result.stderr else ""
            stdout = f"\nStdout: {result.stdout}" if result.stdout else ""
            return f"Command failed (exit code {result.returncode}){stdout}{stderr}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {inp.get('timeout', 30000)}ms"
    except Exception as e:
        return f"Error: {e}"


def _web_fetch(inp: dict) -> str:
    """抓取网页内容。
    - HTML 页面：去除 script/style 标签，提取纯文本
    - 自动解码 HTML 实体（&nbsp; &amp; 等）
    - 结果超过 max_length 时截断
    """
    import urllib.request
    import urllib.error

    url = inp.get("url", "")
    max_length = inp.get("max_length", 50000)
    req = urllib.request.Request(url, headers={"User-Agent": "mini-claude/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"HTTP error: {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error fetching {url}: {e.reason}"
    except Exception as e:
        return f"Error fetching {url}: {e}"

    # HTML 页面：去除脚本、样式、标签，提取纯文本
    if "html" in content_type:
        text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]*>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        # 压缩多余空白
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

    if len(text) > max_length:
        text = text[:max_length] + f"\n\n[... truncated at {max_length} characters]"

    return text or "(empty response)"


# ─── 危险命令模式检测 ─────────────────────────────────────
# 在 default 权限模式下，匹配这些模式的 shell 命令需要用户确认才能执行
# 涵盖 Unix (rm, sudo, kill...) 和 Windows (del, taskkill, Remove-Item...) 双平台

DANGEROUS_PATTERNS = [
    # Unix/Linux 危险命令
    re.compile(r"\brm\s"),                              # 删除文件
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),  # git 破坏性操作
    re.compile(r"\bsudo\b"),                            # 提权执行
    re.compile(r"\bmkfs\b"),                            # 格式化文件系统
    re.compile(r"\bdd\s"),                              # 磁盘写入
    re.compile(r">\s*/dev/"),                           # 写入设备文件
    re.compile(r"\bkill\b"),                            # 终止进程
    re.compile(r"\bpkill\b"),                           # 按名称杀进程
    re.compile(r"\breboot\b"),                          # 重启系统
    re.compile(r"\bshutdown\b"),                        # 关机
    # Windows 危险命令
    re.compile(r"\bdel\s", re.IGNORECASE),              # 删除文件
    re.compile(r"\brmdir\s", re.IGNORECASE),            # 删除目录
    re.compile(r"\bformat\s", re.IGNORECASE),           # 格式化磁盘
    re.compile(r"\btaskkill\s", re.IGNORECASE),         # 终止进程
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),      # PowerShell 删除
    re.compile(r"\bStop-Process\s", re.IGNORECASE),     # PowerShell 杀进程
]


def is_dangerous(command: str) -> bool:
    """检查命令是否匹配任一危险模式。"""
    return any(p.search(command) for p in DANGEROUS_PATTERNS)


# ─── 权限规则系统（.claude/settings.json）─────────────────
# 支持声明式 allow/deny 规则，项目级设置覆盖用户级设置
# 规则格式：tool_name(pattern) 或 tool_name（匹配所有操作）


def _parse_rule(rule: str) -> dict:
    """解析权限规则字符串。
    "run_shell(npm test)" → {"tool": "run_shell", "pattern": "npm test"}
    "write_file" → {"tool": "write_file", "pattern": None}
    """
    m = re.match(r"^([a-z_]+)\((.+)\)$", rule)
    if m:
        return {"tool": m.group(1), "pattern": m.group(2)}
    return {"tool": rule, "pattern": None}


def _load_settings(file_path: Path) -> dict | None:
    """加载 settings.json 文件，文件不存在或解析失败返回 None。"""
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text())
    except Exception:
        return None


_cached_rules: dict | None = None


def load_permission_rules() -> dict:
    """加载并合并用户级和项目级权限规则。
    用户级：~/.claude/settings.json
    项目级：.claude/settings.json
    结果缓存以避免重复读取磁盘。
    """
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules

    allow: list[dict] = []
    deny: list[dict] = []

    user_settings = _load_settings(Path.home() / ".claude" / "settings.json")
    project_settings = _load_settings(Path.cwd() / ".claude" / "settings.json")

    for settings in [user_settings, project_settings]:
        if not settings or "permissions" not in settings:
            continue
        perms = settings["permissions"]
        for r in perms.get("allow", []):
            allow.append(_parse_rule(r))
        for r in perms.get("deny", []):
            deny.append(_parse_rule(r))

    _cached_rules = {"allow": allow, "deny": deny}
    return _cached_rules


def _matches_rule(rule: dict, tool_name: str, inp: dict) -> bool:
    """检查工具调用是否匹配某条权限规则。
    pattern=None 时匹配该工具的所有调用；
    pattern 以 * 结尾时做前缀匹配；否则精确匹配。
    """
    if rule["tool"] != tool_name:
        return False
    if rule["pattern"] is None:
        return True

    # 提取用于匹配的值：shell 用 command，文件操作用 file_path
    value = ""
    if tool_name == "run_shell":
        value = inp.get("command", "")
    elif "file_path" in inp:
        value = inp["file_path"]
    else:
        return True

    pattern = rule["pattern"]
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _check_permission_rules(tool_name: str, inp: dict) -> str | None:
    """根据 allow/deny 规则判断权限。deny 优先于 allow。"""
    rules = load_permission_rules()
    for rule in rules["deny"]:
        if _matches_rule(rule, tool_name, inp):
            return "deny"
    for rule in rules["allow"]:
        if _matches_rule(rule, tool_name, inp):
            return "allow"
    return None


def check_permission(
    tool_name: str,
    inp: dict,
    mode: str = "default",
    plan_file_path: str | None = None,
) -> dict:
    """权限检查的主入口，按优先级顺序判断：
    1. bypassPermissions 模式 → 直接放行
    2. 声明式 allow/deny 规则（deny 优先）
    3. 只读工具 → 放行
    4. plan 模式 → 只允许计划文件编辑，禁止 shell
    5. acceptEdits 模式 → 自动批准编辑工具
    6. default/dontAsk 模式 → 危险命令/新建文件需确认
    返回 {"action": "allow"|"deny"|"confirm", "message": ...}
    """
    if mode == "bypassPermissions":
        return {"action": "allow"}

    # 声明式规则优先
    rule_result = _check_permission_rules(tool_name, inp)
    if rule_result == "deny":
        return {"action": "deny", "message": f"Denied by permission rule for {tool_name}"}
    if rule_result == "allow":
        return {"action": "allow"}

    # 只读工具始终放行
    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    # plan 模式：只允许编辑计划文件本身
    if mode == "plan":
        if tool_name in EDIT_TOOLS:
            file_path = inp.get("file_path") or inp.get("path")
            if plan_file_path and file_path == plan_file_path:
                return {"action": "allow"}
            return {"action": "deny", "message": f"Blocked in plan mode: {tool_name}"}
        if tool_name == "run_shell":
            return {"action": "deny", "message": "Shell commands blocked in plan mode"}

    # enter_plan_mode / exit_plan_mode 始终允许（用于模式切换）
    if tool_name in ("enter_plan_mode", "exit_plan_mode"):
        return {"action": "allow"}

    # acceptEdits 模式：自动批准编辑
    if mode == "acceptEdits" and tool_name in EDIT_TOOLS:
        return {"action": "allow"}

    # 以下为 default/dontAsk 模式下的确认逻辑
    needs_confirm = False
    confirm_message = ""

    if tool_name == "run_shell" and is_dangerous(inp.get("command", "")):
        needs_confirm = True
        confirm_message = inp.get("command", "")
    elif tool_name == "write_file" and not Path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"write new file: {inp.get('file_path', '')}"
    elif tool_name == "edit_file" and not Path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"edit non-existent file: {inp.get('file_path', '')}"

    if needs_confirm:
        if mode == "dontAsk":
            return {"action": "deny", "message": f"Auto-denied (dontAsk mode): {confirm_message}"}
        return {"action": "confirm", "message": confirm_message}

    return {"action": "allow"}


# ─── 工具结果截断 ─────────────────────────────────────────
# 防止超大工具结果撑爆上下文窗口，超过 50000 字符时保留首尾各一半

MAX_RESULT_CHARS = 50000


def _truncate_result(result: str) -> str:
    """截断过长结果，保留首尾各约一半，中间插入截断提示。"""
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        + result[-keep_each:]
    )


# ─── 工具调用执行入口 ─────────────────────────────────────
# agent 和 skill 工具在 agent.py 中处理，避免循环导入。
# 其他工具均在此文件中实现并分发。


async def execute_tool(
    name: str, inp: dict, read_file_state: dict[str, float] | None = None
) -> str:
    """执行工具调用的主入口函数。

    read_file_state: 用于实现"先读后改"安全机制。
    - read_file 时记录文件的 mtime
    - write_file/edit_file 前检查：文件是否已被读取、mtime 是否一致
    - 编辑成功后更新 mtime

    返回工具执行结果字符串（已截断）。
    """
    # ─── 先读后改 + mtime 新鲜度检查 ───────────────────────
    # 防止模型在未读取文件内容的情况下盲目编辑，以及外部并发修改导致的冲突
    if name == "read_file":
        result = _read_file(inp)
        if read_file_state is not None and not result.startswith("Error"):
            abs_path = str(Path(inp["file_path"]).resolve())
            try:
                read_file_state[abs_path] = os.path.getmtime(abs_path)
            except OSError:
                pass
        return _truncate_result(result)

    if name in ("write_file", "edit_file") and read_file_state is not None:
        abs_path = str(Path(inp["file_path"]).resolve())
        if os.path.exists(abs_path):
            if abs_path not in read_file_state:
                verb = "writing" if name == "write_file" else "editing"
                return f"Error: You must read this file before {verb}. Use read_file first to see its current contents."
            if os.path.getmtime(abs_path) != read_file_state[abs_path]:
                verb = "writing" if name == "write_file" else "editing"
                return f"Warning: {inp['file_path']} was modified externally since your last read. Please read_file again before {verb}."

    # tool_search：按关键词搜索延迟工具，激活并返回它们的完整 schema
    if name == "tool_search":
        query = (inp.get("query") or "").lower()
        deferred = [t for t in tool_definitions if t.get("deferred")]
        matches = [
            t for t in deferred
            if query in t["name"].lower() or query in (t.get("description") or "").lower()
        ]
        if not matches:
            return "No matching deferred tools found."
        for m in matches:
            _activated_tools.add(m["name"])
        return json.dumps(
            [{"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"]} for t in matches],
            indent=2,
        )

    # 常规工具：通过 handlers 字典分发
    handlers: dict = {
        "write_file": _write_file,
        "edit_file": _edit_file,
        "list_files": _list_files,
        "grep_search": _grep_search,
        "run_shell": _run_shell,
        "web_fetch": _web_fetch,
    }
    handler = handlers.get(name)
    if not handler:
        return f"Unknown tool: {name}"
    result = _truncate_result(handler(inp))

    # 编辑成功后更新 mtime，确保后续编辑能通过新鲜度检查
    if name in ("write_file", "edit_file") and read_file_state is not None and not result.startswith("Error"):
        abs_path = str(Path(inp["file_path"]).resolve())
        try:
            read_file_state[abs_path] = os.path.getmtime(abs_path)
        except OSError:
            pass

    return result


def reset_permission_cache() -> None:
    """重置权限规则缓存（通常在 settings.json 变更后调用）。"""
    global _cached_rules
    _cached_rules = None
