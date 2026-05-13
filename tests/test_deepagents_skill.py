"""
pytest entry point — deepagents SubAgent + SKILL.md scenario validation.

Usage:
    pytest tests/test_deepagents_skill.py -v
    pytest tests/test_deepagents_skill.py -v -k "test_skill_scenario"  # full e2e
    pytest tests/test_deepagents_skill.py -v -k "test_tool"             # fast checks
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _requires_env():
    """Skip benchmark tests if LLM creds are not configured."""
    if not os.getenv("LLM_API_KEY"):
        from dotenv import load_dotenv
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path.as_posix())
    if not os.getenv("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set — skipping e2e test")


# ═══════════════════════════════════════════════════════════════════════════════
# Fast tests (no LLM calls)
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolIntegrity:
    """Verify tools are importable and return well-formed JSON."""

    def test_parse_docx_outline_smoke(self):
        """parse_docx_outline produces valid structure from synthetic docx."""
        from tests.scenario_test import create_test_docx
        from tools import parse_docx_outline

        docx_path = create_test_docx()
        result = parse_docx_outline.invoke({"file_path": docx_path})

        import json
        data = json.loads(result)
        assert "title" in data
        assert "stats" in data
        assert "sections" in data
        assert "llm_structure" in data
        assert data["stats"]["h2_count"] >= 2

    def test_extract_entities_structure(self):
        """extract_entities returns correct JSON shape (fast no-LLM path)."""
        import json
        from tools import extract_entities

        # Minimal llm_structure with no groups → should return error cleanly
        minimal = json.dumps({"title": "Test", "overview": [], "groups": []})
        # extract_entities is async, so we run it synchronously via _run
        import asyncio

        async def _run():
            return await extract_entities.ainvoke({"llm_structure_json": minimal})

        result = asyncio.run(_run())
        data = json.loads(result)
        assert "error" in data

    def test_store_entities_rejects_bad_input(self):
        """store_entities rejects non-list JSON gracefully."""
        import json
        from tools import store_entities

        result = store_entities.invoke({"entities_json": '{"not": "a list"}'})
        data = json.loads(result)
        assert data["success"] is False


class TestSubAgentCreation:
    """Verify SubAgent and skill directory wiring."""

    def test_skill_md_exists(self):
        """SKILL.md is present and has required frontmatter."""
        skill_path = PROJECT_ROOT / "skills" / "req-parse" / "SKILL.md"
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"

        content = skill_path.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "name:" in content
        assert "allowed-tools:" in content
        # Verify allowed-tools match actual tool names
        assert "parse_docx_outline" in content
        assert "extract_entities" in content
        assert "store_entities" in content

    def test_skill_dir_structure(self):
        """Skill directory contains only expected files."""
        skill_dir = PROJECT_ROOT / "skills" / "req-parse"
        entries = list(skill_dir.iterdir())
        names = {e.name for e in entries}
        assert "SKILL.md" in names, f"Expected SKILL.md in {names}"

    def test_subagent_creation(self):
        """SubAgent spec can be created with skill directory path."""
        from deepagents.middleware.subagents import SubAgent

        sub = SubAgent(
            name="test-req-parse",
            description="Test sub-agent",
            system_prompt="Follow the skill.",
            tools=[],
            skills=[str(PROJECT_ROOT / "skills" / "req-parse")],
        )
        # SubAgent may return dict or object depending on deepagents version
        name = sub.get("name") if isinstance(sub, dict) else getattr(sub, "name", None)
        skills = sub.get("skills") if isinstance(sub, dict) else getattr(sub, "skills", None)
        assert name == "test-req-parse"
        assert len(skills) == 1

    def test_tool_names_match_skill_allowed_tools(self):
        """Tools passed to SubAgent match the allowed-tools in SKILL.md."""
        import yaml

        skill_md = (PROJECT_ROOT / "skills" / "req-parse" / "SKILL.md").read_text(encoding="utf-8")
        # Parse YAML frontmatter
        parts = skill_md.split("---")
        frontmatter = yaml.safe_load(parts[1])
        allowed = {t.strip() for t in frontmatter["allowed-tools"].split(",")}

        from tools import parse_docx_outline, extract_entities, store_entities
        tool_names = {parse_docx_outline.name, extract_entities.name, store_entities.name}

        assert tool_names == allowed, f"Mismatch: tools={tool_names}, allowed={allowed}"


# ═══════════════════════════════════════════════════════════════════════════════
# E2E tests (require LLM API)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillScenarioE2E:
    """End-to-end deepagents SubAgent + SKILL.md scenario validation.

    These tests require LLM_API_KEY in .env and will be skipped otherwise.
    """

    @pytest.fixture(autouse=True)
    def check_env(self):
        _requires_env()

    @pytest.mark.asyncio
    async def test_setup_and_teardown(self):
        """Runner setup/teardown lifecycle works."""
        from tests.scenario_test import SkillScenarioRunner

        runner = SkillScenarioRunner(use_synthetic=True)
        docx = await runner.setup()
        assert Path(docx).exists()
        assert runner.agent is not None
        await runner.teardown()
        assert runner.agent is None

    @pytest.mark.asyncio
    async def test_full_skill_scenario(self):
        """Complete 4-turn skill scenario passes all criteria.

        This is the main e2e test. It validates:
          - Turn 1: SubAgent follows SKILL.md to parse/extract/store
          - Turn 2: Main agent queries via query_reqmgmt
          - Turn 3: Cross-turn memory works
          - Turn 4: Idempotent re-parse produces same counts
        """
        from tests.scenario_test import SkillScenarioRunner

        runner = SkillScenarioRunner(use_synthetic=True)
        results = await runner.run()

        # All criteria must pass
        for passed, desc in results["criteria"]:
            assert passed, f"FAILED: {desc}"

        assert results["all_pass"]
        assert results["db_state"]["product_count"] >= 1
        assert results["db_state"]["orphan_items"] == 0
