"""Tests for hive agent prompts — validate structure and content."""
import os
import re
import subprocess
import sys
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)

import hive_agents

import pytest


AGENTS_DIR = "lib/agents"

# G7: the fixed-six VP files are fragments; their real prompt is the
# composed render (base + fragment) from lib/hive_agents.py.
VP_FRAGMENT_DEPTS = {
    "hr": "vp-hr.md", "it": "vp-it.md", "rd": "vp-rd.md",
    "marketing": "vp-marketing.md", "legal": "vp-legal.md",
    "finance": "vp-finance.md",
}


def _prompt_content(fname):
    """Content the per-file structure checks run against.

    For the fixed-six VP files that is the composed render (the fragment
    on disk is not a prompt by itself); for everything else the raw file.
    """
    for dept, frag_name in VP_FRAGMENT_DEPTS.items():
        if fname == frag_name:
            return hive_agents.compose_dept_vp(dept)
    return Path(f"{AGENTS_DIR}/{fname}").read_text()


class TestAgentFilesExist:
    """Test that all expected agent files exist."""

    EXPECTED_AGENTS = [
        "ceo.md",
        "manager.md",
        "tech-lead.md",
        "qa.md",
        "marketing-liaison.md",
        "marketing-reporter.md",
        "marketing-researcher.md",
        "ic-admin.md",
        "ic-engineer.md",
        "ic-infra.md",
        "ic-tester.md",
        "verification-ic-admin.md",
        "verification-ic.md",
        "verification-manager.md",
        "release-ic.md",
        "vp-dept.md",
        "vp-finance.md",
        "vp-hr.md",
        "vp-it.md",
        "vp-legal.md",
        "vp-marketing.md",
        "vp-rd.md",
    ]

    @pytest.mark.parametrize("agent", EXPECTED_AGENTS)
    def test_agent_file_exists(self, agent):
        assert Path(f"{AGENTS_DIR}/{agent}").exists(), f"Missing agent file: {agent}"


class TestAgentPromptsUsePlainToolNames:
    """Item 2: the MCP server registers PLAIN tool names (ticket_get, …);
    the prompts taught mcp__hive__<tool>, so every prefixed call failed at
    the server with 'Unknown tool: mcp__hive__…' and burned the spawn's
    first turn (12 failed calls in the 2026-11-06 field run)."""

    @pytest.mark.parametrize("fname",
                             sorted(p.name for p in Path(AGENTS_DIR).glob("*.md")))
    def test_no_prefixed_tool_names(self, fname):
        content = _prompt_content(fname)
        assert "mcp__" not in content, (
            f"{fname} teaches the mcp__-prefixed tool spelling — the MCP "
            f"server registers plain names and rejects the prefix"
        )

    def test_framework_primer_documents_plain_names(self):
        primer = Path(f"{AGENTS_DIR}/_FRAMEWORK_PRIMER.md").read_text()
        assert "mcp__hive__" not in primer


class TestAgentPromptStructure:
    """Test that agent prompts have proper structure."""

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md")
    ])
    def test_agent_file_is_not_empty(self, agent):
        content = _prompt_content(agent)
        assert len(content.strip()) > 50, f"{agent} is too short ({len(content)} chars)"

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md")
    ])
    def test_agent_file_has_title(self, agent):
        content = _prompt_content(agent)
        # Should have a markdown title (first line starting with #)
        first_line = content.strip().split("\n")[0]
        assert first_line.startswith("#"), f"{agent} missing title"

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md")
    ])
    def test_agent_file_has_sections(self, agent):
        content = _prompt_content(agent)
        # Should have at least one section header (## or ###)
        has_section = bool(re.search(r"^#{2,}\s", content, re.MULTILINE))
        assert has_section, f"{agent} missing section headers"


class TestFrameworkPrimer:
    """Test the framework primer document."""

    def test_framework_primer_exists(self):
        assert Path(f"{AGENTS_DIR}/_FRAMEWORK_PRIMER.md").exists()

    def test_framework_primer_is_comprehensive(self):
        content = Path(f"{AGENTS_DIR}/_FRAMEWORK_PRIMER.md").read_text()
        # Should be a substantial document
        assert len(content) > 5000, f"_FRAMEWORK_PRIMER.md too short ({len(content)} chars)"

    def test_framework_primer_mentions_stages(self):
        content = Path(f"{AGENTS_DIR}/_FRAMEWORK_PRIMER.md").read_text()
        # Should mention sprint stages
        assert "ceo-plan" in content or "tdd-red" in content


class TestCEOPrompt:
    """Test the CEO agent prompt."""

    def test_ceo_prompt_exists(self):
        assert Path(f"{AGENTS_DIR}/ceo.md").exists()

    def test_ceo_prompt_mentions_stages(self):
        content = Path(f"{AGENTS_DIR}/ceo.md").read_text()
        # CEO should know about all sprint stages
        for stage in ["ceo-plan", "vp-plan", "tdd-red", "verify", "build-and-test", "release-verify"]:
            assert stage in content, f"CEO prompt missing stage: {stage}"

    def test_ceo_prompt_mentions_mcp_tools(self):
        content = Path(f"{AGENTS_DIR}/ceo.md").read_text()
        assert "MCP" in content or "mcp" in content


class TestVerificationICPrompt:
    """Test the verification IC prompt."""

    def test_verification_ic_exists(self):
        assert Path(f"{AGENTS_DIR}/verification-ic.md").exists()

    def test_verification_ic_mentions_approve(self):
        content = Path(f"{AGENTS_DIR}/verification-ic.md").read_text()
        assert "ticket_approve" in content or "ticket_approve" in content.lower()

    def test_verification_ic_mentions_bounce(self):
        content = Path(f"{AGENTS_DIR}/verification-ic.md").read_text()
        assert "ticket_bounce" in content or "ticket_bounce" in content.lower()

    def test_verification_ic_rationale_length(self):
        """Verification IC should know about rationale length requirements."""
        content = Path(f"{AGENTS_DIR}/verification-ic.md").read_text()
        # Should mention 30 chars for approve, 80 chars for bounce
        assert "30" in content or "80" in content


class TestICEngineerPrompt:
    """Test the IC Engineer prompt."""

    def test_ic_engineer_exists(self):
        assert Path(f"{AGENTS_DIR}/ic-engineer.md").exists()

    def test_ic_engineer_mentions_tdd(self):
        content = Path(f"{AGENTS_DIR}/ic-engineer.md").read_text()
        assert "TDD" in content or "tdd" in content.lower()

    def test_ic_engineer_mentions_worktree(self):
        content = Path(f"{AGENTS_DIR}/ic-engineer.md").read_text()
        assert "worktree" in content.lower()


class TestVPPrompts:
    """Test VP agent prompts."""

    @pytest.mark.parametrize("dept", ["it", "rd", "hr", "marketing", "legal", "finance"])
    def test_vp_prompt_exists(self, dept):
        assert Path(f"{AGENTS_DIR}/vp-{dept}.md").exists()

    def test_vp_prompts_mention_sprint_stages(self):
        """All VP prompts should know about sprint stages."""
        for f in Path(AGENTS_DIR).glob("vp-*.md"):
            content = _prompt_content(f.name)
            assert "tdd-red" in content or "verify" in content, f"{f.name} missing stage info"


class TestAgentPromptsHaveMCPHeader:
    """Test that agent prompts have the MCP tools header."""

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md") if not p.name.startswith("_")
    ])
    def test_agent_mentions_mcp_tools(self, agent):
        content = _prompt_content(agent)
        # Most agents should reference MCP tools
        has_mcp_ref = "mcp__" in content or "MCP" in content or "tool_" in content
        assert has_mcp_ref, f"{agent} doesn't mention MCP tools"


class TestAgentPromptsHaveTicketFlow:
    """Test that agent prompts describe ticket workflow."""

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md") if not p.name.startswith("_")
    ])
    def test_agent_mentions_ticket_statuses(self, agent):
        content = _prompt_content(agent)
        # Agents should know about ticket statuses
        has_status = any(s in content for s in ["in-progress", "in-review", "sprint-current", "backlog"])
        assert has_status, f"{agent} doesn't mention ticket statuses"


class TestAgentPromptsHaveConsistentFormatting:
    """Test that agent prompts use consistent formatting."""

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md") if not p.name.startswith("_")
    ])
    def test_agent_uses_code_blocks(self, agent):
        content = _prompt_content(agent)
        # Should have at least some code examples
        has_code = "```" in content or "`" in content
        assert has_code, f"{agent} has no code references"


class TestAgentPromptsHaveErrorHandling:
    """Test that agent prompts mention error handling."""

    def test_ceo_mentions_error_handling(self):
        content = Path(f"{AGENTS_DIR}/ceo.md").read_text()
        # CEO should know about error recovery
        has_recovery = "recover" in content.lower() or "error" in content.lower() or "fail" in content.lower()
        assert has_recovery


class TestAgentCount:
    """Test that we have a reasonable number of agents."""

    def test_agent_count(self):
        agent_files = list(Path(AGENTS_DIR).glob("*.md"))
        # Should have at least 15 agent files (excluding _FRAMEWORK_PRIMER.md)
        regular_agents = [f for f in agent_files if not f.name.startswith("_")]
        assert len(regular_agents) >= 15, f"Expected >= 15 agents, got {len(regular_agents)}"


class TestAgentPromptsNoPlaceholders:
    """Test that agent prompts don't contain TODO placeholders."""

    @pytest.mark.parametrize("agent", [
        str(p.name) for p in Path(AGENTS_DIR).glob("*.md") if not p.name.startswith("_")
    ])
    def test_agent_has_no_todos(self, agent):
        content = _prompt_content(agent)
        # Should not have TODO or FIXME markers
        assert "TODO" not in content and "FIXME" not in content, f"{agent} contains TODO/FIXME"


class TestVPComposition:
    """G7: the fixed-six VP prompts are composed from vp-dept.md (base) +
    a per-dept fragment (title line + scope section) by
    lib/hive_agents.py. The goldens in tests/golden/ were captured from
    the pre-conversion static files: the five non-IT goldens are
    byte-identical to those files; the IT golden is the old file plus the
    restored 'hive friction -> IT ticket' sentence (the one deliberate
    content change of G7)."""

    @pytest.mark.parametrize("dept", sorted(VP_FRAGMENT_DEPTS))
    def test_composed_render_matches_golden(self, dept):
        golden = Path("tests/golden/vp-%s-composed.md" % dept).read_text()
        composed = hive_agents.compose_dept_vp(dept)
        assert composed == golden, (
            f"vp-{dept} composition drifted from the golden — re-capture "
            f"only with an intentional content change"
        )

    @pytest.mark.parametrize("dept", sorted(VP_FRAGMENT_DEPTS))
    def test_fragment_is_a_fragment(self, dept):
        frag = Path(f"{AGENTS_DIR}/vp-{dept}.md").read_text()
        lines = frag.splitlines()
        assert len(lines) < 60, f"vp-{dept}.md fragment is {len(lines)} lines (bound: 60)"
        assert lines[0].startswith("#"), "fragment must start with the title line"
        assert re.search(r"^## .*-specific scope$", frag, re.MULTILINE), \
            f"vp-{dept}.md missing its scope section"

    def test_dynamic_dept_gets_base_verbatim(self):
        """Dynamic departments (HR-created depts) spawn the base as-is."""
        base = Path(f"{AGENTS_DIR}/vp-dept.md").read_text()
        assert hive_agents.compose_dept_vp("frontend-web") == base

    def test_base_is_a_complete_standalone_prompt(self):
        """vp-dept.md is itself a live prompt (the dynamic-dept fallback),
        so it must keep its tail sections — not just its head."""
        base = Path(f"{AGENTS_DIR}/vp-dept.md").read_text()
        for needle in [
            "## Hard rules (script-enforced)",
            "## Allowed side actions",
            "### Then exit",
            "next sprint's stage 1 (ceo-plan) runs.",
        ]:
            assert needle in base, f"vp-dept.md missing {needle!r}"

    def test_bash_spawn_path_gets_primer_plus_composed_prompt(self):
        """sprint-lifecycle's spawn path (load_dept_vp_prompt) must deliver
        primer + composed render — the same bytes the goldens pin."""
        repo = Path(__file__).resolve().parent.parent
        out = subprocess.run(
            ["bash", "-c",
             "source lib/tools/load-agent-prompt.sh && load_dept_vp_prompt it"],
            capture_output=True, text=True, cwd=str(repo),
            env={**os.environ, "SKILL_DIR": str(repo)},
            timeout=30,
        )
        assert out.returncode == 0, out.stderr
        primer = (repo / "lib" / "agents" / "_FRAMEWORK_PRIMER.md").read_text()
        golden = (repo / "tests" / "golden" / "vp-it-composed.md").read_text()
        assert out.stdout == primer + "\n" + golden
