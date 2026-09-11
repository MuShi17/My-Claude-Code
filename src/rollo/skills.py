"""Skills 系统 — 发现、解析和执行 .rollo/skills/*/SKILL.md
使用 frontmatter 元数据与提示词模板定义可发现的技能。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import parse_frontmatter
from .project_context import ProjectContext

# ─── Types ──────────────────────────────────────────────────


@dataclass
class SkillDefinition:
    name: str
    description: str
    when_to_use: str | None = None
    allowed_tools: list[str] | None = None
    user_invocable: bool = True
    context: str = "inline"  # "inline" or "fork"
    prompt_template: str = ""
    source: str = "project"  # "project" or "user"
    skill_dir: str = ""


# ─── Discovery ──────────────────────────────────────────────

_cached_skills: dict[str, list[SkillDefinition]] = {}


def _resolve_context(context: "ProjectContext | None") -> "ProjectContext":
    """解析消费方 context：显式优先，缺省按调用点 cwd 一次性构造（不缓存、不共享）。"""

    if context is not None:
        return context
    return ProjectContext.from_root(Path.cwd())


def discover_skills(context: "ProjectContext | None" = None) -> list[SkillDefinition]:
    resolved = _resolve_context(context)
    cached = _cached_skills.get(resolved.workspace_id)
    if cached is not None:
        return cached

    skills: dict[str, SkillDefinition] = {}

    # User-level skills (lower priority)
    user_dir = Path.home() / ".rollo" / "skills"
    _load_skills_from_dir(user_dir, "user", skills)
    
    # 加载CC插件目录下的技能文件
    # plugins_dir = Path.home() / ".rollo" / "plugins"
    # _load_skills_from_dir(plugins_dir, "user", skills)

    # Project-level skills (higher priority, overwrites)
    project_dir = resolved.skills_dir
    _load_skills_from_dir(project_dir, "project", skills)

    _cached_skills[resolved.workspace_id] = list(skills.values())
    return _cached_skills[resolved.workspace_id]


def _load_skills_from_dir(
    base_dir: Path, source: str, skills: dict[str, SkillDefinition]
) -> None:
    if not base_dir.is_dir():
        return
    # rglob 递归匹配所有 SKILL.md 文件
    for skill_file in base_dir.rglob("SKILL.md"):
        # 获取 SKILL.md 所在的目录
        skill_dir = skill_file.parent
        skill = _parse_skill_file(skill_file, source, str(skill_dir))
        if skill:
            skills[skill.name] = skill


def _parse_skill_file(
    file_path: Path, source: str, skill_dir: str
) -> SkillDefinition | None:
    try:
        raw = file_path.read_text(encoding="utf-8")
        result = parse_frontmatter(raw)
        meta = result.meta

        name = meta.get("name") or file_path.parent.name or "unknown"
        user_invocable = meta.get("user-invocable", "true") != "false"
        context = "fork" if meta.get("context") == "fork" else "inline"

        allowed_tools: list[str] | None = None
        if "allowed-tools" in meta:
            raw_tools = meta["allowed-tools"]
            if raw_tools.startswith("["):
                try:
                    allowed_tools = json.loads(raw_tools)
                except Exception:
                    allowed_tools = [s.strip() for s in raw_tools.strip("[]").split(",")]
            else:
                allowed_tools = [s.strip() for s in raw_tools.split(",")]

        return SkillDefinition(
            name=name,
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use") or meta.get("when-to-use"),
            allowed_tools=allowed_tools,
            user_invocable=user_invocable,
            context=context,
            prompt_template=result.body,
            source=source,
            skill_dir=skill_dir,
        )
    except Exception:
        return None


# ─── Resolution ─────────────────────────────────────────────


def get_skill_by_name(name: str, context: ProjectContext | None = None) -> SkillDefinition | None:
    for s in discover_skills(context):
        if s.name == name:
            return s
    return None


def resolve_skill_prompt(skill: SkillDefinition, args: str) -> str:
    import re
    prompt = skill.prompt_template
    prompt = re.sub(r"\$ARGUMENTS|\$\{ARGUMENTS\}", args, prompt)
    prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.skill_dir)
    return prompt


def execute_skill(
    skill_name: str, args: str, context: ProjectContext | None = None
) -> dict | None:
    skill = get_skill_by_name(skill_name, context)
    if not skill:
        return None
    return {
        "prompt": resolve_skill_prompt(skill, args),
        "allowed_tools": skill.allowed_tools,
        "context": skill.context,
    }


# ─── System prompt section ──────────────────────────────────


def build_skill_descriptions(context: ProjectContext | None = None) -> str:
    skills = discover_skills(context)
    if not skills:
        return ""

    lines = ["# Available Skills", ""]
    invocable = [s for s in skills if s.user_invocable]
    auto_only = [s for s in skills if not s.user_invocable]

    if invocable:
        lines.append("User-invocable skills (user types /<name> to invoke):")
        for s in invocable:
            lines.append(f"- **/{s.name}**: {s.description}")
            if s.when_to_use:
                lines.append(f"  When to use: {s.when_to_use}")
        lines.append("")

    if auto_only:
        lines.append("Auto-invocable skills (use the skill tool when appropriate):")
        for s in auto_only:
            lines.append(f"- **{s.name}**: {s.description}")
            if s.when_to_use:
                lines.append(f"  When to use: {s.when_to_use}")
        lines.append("")

    lines.append("To invoke a skill programmatically, use the `skill` tool with the skill name and optional arguments.")
    return "\n".join(lines)


def reset_skill_cache() -> None:
    _cached_skills.clear()
