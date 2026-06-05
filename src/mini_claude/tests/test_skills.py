"""Test suite for skills.py — covers discovery, parsing, resolution, and override logic."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from mini_claude.skills import (
    SkillDefinition,
    _load_skills_from_dir,
    _parse_skill_file,
    build_skill_descriptions,
    discover_skills,
    execute_skill,
    get_skill_by_name,
    reset_skill_cache,
    resolve_skill_prompt,
)


# ─── helpers ──────────────────────────────────────────────────


def _make_skill(
    base: Path, name: str, meta: dict[str, str] | None = None, body: str = ""
) -> Path:
    """Create a SKILL.md under `base/<name>/SKILL.md` and return the skill dir."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    if meta:
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
    lines += ["---", body]
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


def _patch_paths(user_dir: Path, project_dir: Path):
    """Return patch objects that divert Path.home() and Path.cwd() to temp dirs."""
    home_patch = patch("mini_claude.skills.Path.home", return_value=user_dir)
    cwd_patch = patch("mini_claude.skills.Path.cwd", return_value=project_dir)
    return home_patch, cwd_patch


# ─── parse_skill_file ──────────────────────────────────────────


class TestParseSkillFile:
    def test_basic_parsing(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "my-skill",
            {"name": "my-skill", "description": "A test skill"},
            body="This is the prompt template.",
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "user", str(skill_file.parent))
        assert skill is not None
        assert skill.name == "my-skill"
        assert skill.description == "A test skill"
        assert skill.source == "user"
        assert skill.prompt_template == "This is the prompt template."
        assert skill.context == "inline"
        assert skill.user_invocable is True
        assert skill.skill_dir == str(skill_file.parent)

    def test_falls_back_to_dirname_when_no_name_key(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "unnamed-skill",
            {"description": "no name field"},
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "project", str(skill_file.parent))
        assert skill is not None
        assert skill.name == "unnamed-skill"

    def test_user_invocable_false(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "hidden",
            {"name": "hidden", "description": "auto-only", "user-invocable": "false"},
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "user", str(skill_file.parent))
        assert skill is not None
        assert skill.user_invocable is False

    def test_fork_context(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "forked",
            {"name": "forked", "description": "runs in fork", "context": "fork"},
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "project", str(skill_file.parent))
        assert skill is not None
        assert skill.context == "fork"

    def test_allowed_tools_json_list(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "restricted",
            {
                "name": "restricted",
                "description": "limited tools",
                "allowed-tools": '["read_file", "grep_search"]',
            },
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "user", str(skill_file.parent))
        assert skill is not None
        assert skill.allowed_tools == ["read_file", "grep_search"]

    def test_allowed_tools_comma_list(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "restricted2",
            {
                "name": "restricted2",
                "description": "comma tools",
                "allowed-tools": "read_file, grep_search, list_files",
            },
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "user", str(skill_file.parent))
        assert skill is not None
        assert skill.allowed_tools == ["read_file", "grep_search", "list_files"]

    def test_when_to_use_field(self, tmp_path: Path):
        skill_file = _make_skill(
            tmp_path, "on-demand",
            {
                "name": "on-demand",
                "description": "conditional skill",
                "when_to_use": "when user asks for X",
            },
        ) / "SKILL.md"

        skill = _parse_skill_file(skill_file, "user", str(skill_file.parent))
        assert skill is not None
        assert skill.when_to_use == "when user asks for X"

    def test_nonexistent_file(self):
        skill = _parse_skill_file(
            Path("/nonexistent/path/SKILL.md"), "user", "/nonexistent/path"
        )
        assert skill is None


# ─── discovery: ~/.claude/skills/ ──────────────────────────────


class TestDiscoverSkillsUserDir:
    """Verify discover_skills() picks up skills from ~/.claude/skills/*/SKILL.md."""

    def test_single_user_skill(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        _make_skill(
            user_home / ".claude" / "skills", "user-skill",
            {"name": "user-skill", "description": "from home dir"},
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "user-skill"
        assert skills[0].description == "from home dir"
        assert skills[0].source == "user"

    def test_multiple_user_skills(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        skills_dir = user_home / ".claude" / "skills"
        _make_skill(skills_dir, "skill-a", {"name": "skill-a", "description": "A"})
        _make_skill(skills_dir, "skill-b", {"name": "skill-b", "description": "B"})

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill-a", "skill-b"}

    def test_no_skills_when_dir_missing(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        # Don't create .claude/skills/

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert skills == []

    def test_skips_directories_without_skill_md(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        skills_dir = user_home / ".claude" / "skills"
        # Create a directory that has no SKILL.md
        empty_skill_dir = skills_dir / "empty-skill"
        empty_skill_dir.mkdir(parents=True)
        # Create a proper skill
        _make_skill(skills_dir, "real-skill", {"name": "real-skill", "description": "real"})

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "real-skill"

    def test_skips_non_directory_entries(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        skills_dir = user_home / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        # Create a file (not a directory) — should be skipped
        (skills_dir / "some-file.txt").write_text("not a skill dir")
        # Create a real skill
        _make_skill(skills_dir, "real-skill", {"name": "real-skill", "description": "real"})

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "real-skill"


# ─── discovery: .claude/skills/ (project) ─────────────────────


class TestDiscoverSkillsProjectDir:
    def test_project_skill_discovered(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        _make_skill(
            project_dir / ".claude" / "skills", "proj-skill",
            {"name": "proj-skill", "description": "from project"},
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "proj-skill"
        assert skills[0].source == "project"


# ─── project overrides user ──────────────────────────────────


class TestSkillOverride:
    def test_project_skill_overrides_user_skill_same_name(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        _make_skill(
            user_home / ".claude" / "skills", "shared-skill",
            {"name": "shared-skill", "description": "user version"},
        )
        _make_skill(
            project_dir / ".claude" / "skills", "shared-skill",
            {"name": "shared-skill", "description": "project version (overrides)"},
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skills = discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "shared-skill"
        assert skills[0].description == "project version (overrides)"
        assert skills[0].source == "project"


# ─── get_skill_by_name ───────────────────────────────────────


class TestGetSkillByName:
    def test_found(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _make_skill(
            user_home / ".claude" / "skills", "target",
            {"name": "target", "description": "found"},
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skill = get_skill_by_name("target")

        assert skill is not None
        assert skill.name == "target"

    def test_not_found(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            skill = get_skill_by_name("missing")

        assert skill is None


# ─── resolve_skill_prompt ─────────────────────────────────────


def test_resolve_skill_prompt_replaces_arguments():
    skill = SkillDefinition(
        name="test",
        description="",
        prompt_template="Use args: $ARGUMENTS",
    )
    result = resolve_skill_prompt(skill, "some args")
    assert result == "Use args: some args"


def test_resolve_skill_prompt_replaces_braced_arguments():
    skill = SkillDefinition(
        name="test",
        description="",
        prompt_template="Braced: ${ARGUMENTS}",
    )
    result = resolve_skill_prompt(skill, "braced args")
    assert result == "Braced: braced args"


def test_resolve_skill_prompt_replaces_skill_dir():
    skill = SkillDefinition(
        name="test",
        description="",
        prompt_template="Skill dir is: ${CLAUDE_SKILL_DIR}",
        skill_dir="/path/to/skill",
    )
    result = resolve_skill_prompt(skill, "")
    assert result == "Skill dir is: /path/to/skill"


# ─── execute_skill ───────────────────────────────────────────


class TestExecuteSkill:
    def test_execute_existing_skill(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _make_skill(
            user_home / ".claude" / "skills", "cmd",
            {"name": "cmd", "description": "a command", "context": "fork"},
            body="Execute with: $ARGUMENTS",
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            result = execute_skill("cmd", "--help")

        assert result is not None
        assert result["context"] == "fork"
        assert "Execute with: --help" in result["prompt"]

    def test_execute_nonexistent_skill(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            result = execute_skill("ghost", "")

        assert result is None


# ─── build_skill_descriptions ─────────────────────────────────


class TestBuildSkillDescriptions:
    def test_empty_when_no_skills(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            result = build_skill_descriptions()

        assert result == ""

    def test_user_invocable_listed(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _make_skill(
            user_home / ".claude" / "skills", "greet",
            {"name": "greet", "description": "says hello", "when_to_use": "greeting"},
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            result = build_skill_descriptions()

        assert "**/greet**" in result
        assert "says hello" in result
        assert "When to use: greeting" in result

    def test_auto_only_listed(self, tmp_path: Path):
        user_home = tmp_path / "home"
        user_home.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _make_skill(
            user_home / ".claude" / "skills", "auto-run",
            {
                "name": "auto-run",
                "description": "runs automatically",
                "user-invocable": "false",
            },
        )

        reset_skill_cache()
        with patch("mini_claude.skills.Path.home", return_value=user_home), \
             patch("mini_claude.skills.Path.cwd", return_value=project_dir):
            result = build_skill_descriptions()

        assert "**auto-run**" in result
        assert "runs automatically" in result
        # Should NOT contain the user-invocable header for this skill
        assert "User-invocable skills" not in result


# ─── cache ───────────────────────────────────────────────────


def test_discover_skills_uses_cache(tmp_path: Path):
    """Calling discover_skills twice should hit cache — only one file-system read."""
    user_home = tmp_path / "home"
    user_home.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_skill(
        user_home / ".claude" / "skills", "cached",
        {"name": "cached", "description": "cache test"},
    )

    reset_skill_cache()
    with patch("mini_claude.skills.Path.home", return_value=user_home), \
         patch("mini_claude.skills.Path.cwd", return_value=project_dir):
        s1 = discover_skills()
        s2 = discover_skills()

    assert s1 is s2  # same list object (cached)


def test_reset_skill_cache_invalidates(tmp_path: Path):
    user_home = tmp_path / "home"
    user_home.mkdir()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _make_skill(
        user_home / ".claude" / "skills", "refresh",
        {"name": "refresh", "description": "refresh test"},
    )

    reset_skill_cache()
    with patch("mini_claude.skills.Path.home", return_value=user_home), \
         patch("mini_claude.skills.Path.cwd", return_value=project_dir):
        s1 = discover_skills()
        reset_skill_cache()
        s2 = discover_skills()

    assert s1 is not s2
    assert len(s1) == len(s2)
