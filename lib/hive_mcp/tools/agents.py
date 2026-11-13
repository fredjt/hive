"""Agent spawning tools.

These spawn agents via lib/core/agent-spawn.sh (built-in harness). The wrapper
injects an MCP config so spawned agents can call this same hive MCP server.
"""

from __future__ import annotations

import os

from hive_agents import FIXED_DEPTS, compose_dept_vp

from hive_mcp.shell import HIVE_ROOT


def _agent_spawn_path() -> str:
    return str(HIVE_ROOT / "lib" / "core" / "agent-spawn.sh")


def register(server: Server) -> None:
    @server.tool()
    def agent_spawn_ceo(project_root: str, mode: str = "FRESH") -> dict:
        """Spawn the CEO agent.

        `mode` is FRESH (initial build) or RESUME (existing manifest).
        Returns the CEO's JSON output.
        """
        ceo_md = HIVE_ROOT / "lib" / "agents" / "ceo.md"
        prompt = ceo_md.read_text() + f"\n\n--- Assignment ---\nProject root: {project_root}\nMode: {mode}\n"
        from subprocess import run as srun
        result = srun(
            ["bash", _agent_spawn_path()],
            input=prompt,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HIVE_ROOT":  str(HIVE_ROOT),
                "HIVE_AGENT": "ceo",
                "HIVE_DEPT":  "executive",
                "HIVE_ROLE":  "CEO",
            },
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @server.tool()
    def agent_run_vp(dept: str, project_root: str) -> dict:
        """Run one cycle of the VP agent for a department.

        Used both during build and for ad-hoc cycles. The perpetual loop is
        managed by `company_start`.
        """
        # G7: the fixed-six VP prompts are composed from vp-dept.md + the
        # per-dept fragment (hive_agents is the single implementation; the
        # sprint-lifecycle spawn path renders the same bytes). Dynamic
        # depts get the base verbatim.
        agent_name = f"vp-{dept}" if dept in FIXED_DEPTS else "vp-dept"
        prompt = (
            compose_dept_vp(dept, lib_dir=HIVE_ROOT / "lib")
            + f"\n\n--- Assignment ---\nDepartment: {dept}\nProject root: {project_root}\n"
        )
        from subprocess import run as srun
        result = srun(
            ["bash", _agent_spawn_path()],
            input=prompt,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HIVE_ROOT":  str(HIVE_ROOT),
                "HIVE_AGENT": agent_name,
                "HIVE_DEPT":  dept,
                "HIVE_ROLE":  "VP",
            },
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    @server.tool()
    def agent_run_ic(
        agent_name: str,
        dept: str,
        ticket_id: str,
        project_root: str,
        prompt_extra: str = "",
    ) -> dict:
        """Spawn an IC (Individual Contributor) agent for a single ticket.

        Use this from any VP / manager / orchestrator agent that needs to
        delegate ticket work. The wrapper automatically:
          - Reads the IC's prompt template from lib/agents/<agent_name>.md
          - Tags the spawn with HIVE_AGENT/DEPT/ROLE/TICKET so it shows up
            correctly in the dashboard's Activity panel (instead of inheriting
            the parent's identity)
          - Invokes agent-spawn.sh which injects the hive MCP config so the
            IC sees all 50+ MCP tools

        Args:
          agent_name:  one of "ic-engineer", "ic-infra", "ic-researcher",
                       "ic-tester", "marketing-researcher", "marketing-liaison",
                       "marketing-reporter", "qa", "tech-lead". Maps to the
                       file lib/agents/<agent_name>.md.
          dept:        the department the IC is working for (e.g. "frontend-web")
          ticket_id:   the ticket id the IC should pick up
          project_root: absolute path to the project (where .company/ lives)
          prompt_extra: optional extra text appended to the prompt (e.g. ticket
                       JSON content, worktree path, special instructions)
        """
        from subprocess import run as srun
        prompt_file = HIVE_ROOT / "lib" / "agents" / f"{agent_name}.md"
        if not prompt_file.is_file():
            return {"ok": False, "error": f"agent prompt not found: {prompt_file}"}
        prompt = prompt_file.read_text()
        prompt += (
            f"\n\n--- Assignment ---\n"
            f"Department: {dept}\n"
            f"Ticket: {ticket_id}\n"
            f"Project root: {project_root}\n"
            f"SKILL_DIR: {HIVE_ROOT}\n"
        )
        if prompt_extra:
            prompt += f"\n{prompt_extra}\n"

        result = srun(
            ["bash", _agent_spawn_path()],
            input=prompt,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HIVE_ROOT":   str(HIVE_ROOT),
                "HIVE_AGENT":  agent_name,
                "HIVE_DEPT":   dept,
                "HIVE_ROLE":   "IC",
                "HIVE_TICKET": ticket_id,
            },
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "agent": agent_name,
            "dept": dept,
            "ticket": ticket_id,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    # agent_verify removed under sprint-staged lifecycle: verification ICs are
    # spawned by sprint-lifecycle during the verify stage, not via MCP. Agents
    # asking for ad-hoc verification should not bypass the stage cycle.
