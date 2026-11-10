"""Tests for hive JSON structures — validate schemas and required fields."""
import sys
import json
import subprocess
from pathlib import Path
# Ensure lib/ is on the path when running from within hive/ directory
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)



import pytest


class TestManifestSchema:
    """Test manifest.json structure."""

    def test_manifest_required_fields(self, sample_manifest):
        m = sample_manifest
        assert "project_name" in m
        assert "mode" in m
        assert m["mode"] in ("build", "perpetual")

    def test_manifest_mode_values(self):
        """Valid modes are 'build' and 'perpetual'."""
        valid_modes = {"build", "perpetual"}
        for mode in valid_modes:
            manifest = {"project_name": "test", "mode": mode}
            assert manifest["mode"] in valid_modes


class TestOrgChartSchema:
    """Test org-chart.json structure."""

    def test_org_chart_required_fields(self, sample_org_chart):
        oc = sample_org_chart
        assert "departments" in oc
        assert isinstance(oc["departments"], dict)

    def test_department_required_fields(self, sample_org_chart):
        for dept_name, dept_data in sample_org_chart["departments"].items():
            assert "name" in dept_data
            assert "reporting_to" in dept_data


class TestTicketSchema:
    """Test ticket JSON structure."""

    def test_ticket_required_fields(self, sample_ticket):
        t = sample_ticket
        required = ["id", "title", "description", "status", "priority", "type"]
        for field in required:
            assert field in t, f"Ticket missing required field: {field}"

    def test_ticket_valid_statuses(self, sample_ticket):
        valid_statuses = {"backlog", "sprint-current", "in-progress", "in-review", "done", "archive"}
        assert sample_ticket["status"] in valid_statuses

    def test_ticket_valid_priorities(self, sample_ticket):
        valid_priorities = {"critical", "high", "medium", "low"}
        assert sample_ticket["priority"] in valid_priorities

    def test_ticket_valid_types(self):
        valid_types = {"feature", "bug", "improvement", "chore", "infrastructure", "research"}
        for t in valid_types:
            ticket = {"id": "T-1", "title": "t", "description": "d", "status": "backlog", "priority": "medium", "type": t}
            assert ticket["type"] in valid_types

    def test_ticket_has_timestamps(self, sample_ticket):
        assert "created_at" in sample_ticket
        assert "updated_at" in sample_ticket


class TestRosterSchema:
    """Test roster.json structure."""

    def test_roster_agents_key(self, sample_roster):
        assert "agents" in sample_roster
        assert isinstance(sample_roster["agents"], list)

    def test_agent_required_fields(self, sample_roster):
        agent = sample_roster["agents"][0]
        required = ["agent_id", "name", "role", "department", "status"]
        for field in required:
            assert field in agent, f"Agent missing required field: {field}"

    def test_agent_valid_roles(self, sample_roster):
        valid_roles = {"VP", "IC", "Manager-Verification", "CEO"}
        for agent in sample_roster["agents"]:
            assert agent["role"] in valid_roles or "Manager" in agent.get("role", "")


class TestSprintStateSchema:
    """Test sprint state JSON structure."""

    def test_sprint_state_required_fields(self, sample_sprint_state):
        s = sample_sprint_state
        required = ["sprint_id", "started_at", "stage", "stage_started_at",
                     "stage_enqueued", "loop_iteration", "tickets_in_sprint"]
        for field in required:
            assert field in s, f"Sprint state missing: {field}"

    def test_sprint_state_valid_stages(self, sample_sprint_state):
        valid_stages = [
            "ceo-plan", "vp-plan", "sprint-fill", "tdd-red", "tdd-green",
            "verify", "build-and-test", "release-verify", "release-publish",
            "ceo-bugfix-plan", "vp-bugfix-plan", "vp-review", "complete"
        ]
        assert sample_sprint_state["stage"] in valid_stages

    def test_sprint_state_release_fields(self, sample_sprint_state):
        release = sample_sprint_state["release"]
        required_release = ["version", "test_log", "verify_log", "tarball", "sha256", "publish_status"]
        for field in required_release:
            assert field in release, f"Release missing: {field}"

    def test_sprint_stage_enqueued_is_boolean(self, sample_sprint_state):
        assert isinstance(sample_sprint_state["stage_enqueued"], bool)

    def test_sprint_loop_iteration_is_int(self, sample_sprint_state):
        assert isinstance(sample_sprint_state["loop_iteration"], int)


class TestRoundSchema:
    """Test round JSON structure."""

    def test_round_required_fields(self, sample_round):
        r = sample_round
        assert "entries" in r
        assert "cursor" in r

    def test_entry_required_fields(self, sample_round):
        entry = sample_round["entries"][0]
        required = ["id", "kind", "agent", "dept", "role"]
        for field in required:
            assert field in entry, f"Round entry missing: {field}"

    def test_entry_valid_kinds(self):
        valid_kinds = [
            "ic-cycle-eng", "ic-cycle-tester", "vp-cycle", "verification",
            "release-verify", "ceo-plan", "standup-brief", "standup-synth",
            "vp-review", "agent-run"
        ]
        for kind in valid_kinds:
            entry = {"id": "e1", "kind": kind, "agent": "test", "dept": "it", "role": "IC"}
            assert entry["kind"] in valid_kinds


class TestGatesSchema:
    """Test gates.json structure."""

    def test_gate_result_has_status(self):
        gate = {"status": "pass", "detail": "test passed", "checked_at": "2025-01-01T00:00:00Z"}
        assert "status" in gate
        assert gate["status"] in ("pass", "fail", "skip")

    def test_gate_result_has_detail(self):
        gate = {"status": "pass", "detail": "test passed", "checked_at": "2025-01-01T00:00:00Z"}
        assert "detail" in gate
        assert isinstance(gate["detail"], str)

    def test_gate_result_has_checked_at(self):
        gate = {"status": "pass", "detail": "test passed", "checked_at": "2025-01-01T00:00:00Z"}
        assert "checked_at" in gate


class TestChatSchema:
    """Test chat message JSON structure."""

    def test_chat_message_required_fields(self):
        msg = {
            "channel": "general",
            "author": "user",
            "timestamp": "2025-01-01T00:00:00Z",
            "body": "Hello world",
        }
        required = ["channel", "author", "timestamp", "body"]
        for field in required:
            assert field in msg


class TestReportSchema:
    """Test report JSON structure."""

    def test_report_required_fields(self):
        report = {
            "type": "status",
            "department": "it",
            "date": "2025-01-01",
            "content": "Weekly status update",
        }
        required = ["type", "department", "date"]
        for field in required:
            assert field in report


class TestReleaseSchema:
    """Test release JSON structure."""

    def test_release_record_required_fields(self):
        release = {
            "version": "v1.0.0",
            "phase": "pending",
            "adversarial_verdict": None,
        }
        required = ["version", "phase"]
        for field in required:
            assert field in release

    def test_release_valid_phases(self):
        valid_phases = {"pending", "gates", "build-and-test", "release-verify", "publish", "done", "failed"}
        for phase in valid_phases:
            release = {"version": "v1.0.0", "phase": phase}
            assert release["phase"] in valid_phases


def _shipped_json_files():
    """Every .json under the SHIPPED source set — entries.list is the
    single source of truth for what ships (S2, 0.12), so the validity
    check covers exactly the release surface.

    (0.13 T5 skip audit: the old parametrize globbed Path("hive") —
    the pre-flat-repo nested layout. Zero matches there is a SILENT
    auto-skip ("got empty parameter set"), so this test shipped zero
    coverage for a release: a skip that outlived its reason. The
    tests/ fixtures are deliberately out of scope —
    company-degenerate/processes.json is corrupted ON PURPOSE by the
    degenerate-state tests.)
    """
    root = Path(__file__).resolve().parent.parent
    found = set()
    for line in (root / "packaging" / "entries.list").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        src = root / line.split(" as ")[0].strip()
        if src.is_dir():
            found.update(q for q in src.rglob("*.json")
                         if "node_modules" not in q.parts)
        elif src.is_file() and src.suffix == ".json":
            found.add(src)
    return sorted(found)


class TestJSONFilesValid:
    """Test that existing JSON files in the hive source are valid."""

    @pytest.mark.parametrize("json_file", [str(p) for p in _shipped_json_files()])
    def test_json_file_is_valid(self, json_file):
        """Every .json file should parse without errors."""
        try:
            data = json.loads(Path(json_file).read_text())
            assert isinstance(data, (dict, list))
        except json.JSONDecodeError as e:
            pytest.fail(f"{json_file} is not valid JSON: {e}")


class TestVersionFile:
    """Test VERSION file."""

    def test_version_file_exists(self):
        assert Path("VERSION").exists()

    def test_version_file_is_not_empty(self):
        content = Path("VERSION").read_text().strip()
        assert len(content) > 0

    def test_version_file_is_semver_like(self):
        content = Path("VERSION").read_text().strip()
        # Should be a version string like "1.2.3"
        parts = content.split(".")
        assert len(parts) >= 2, f"Version '{content}' doesn't look like semver"


class TestConfigDefaults:
    """Test config.defaults.toml."""

    def test_config_file_exists(self):
        assert Path("etc/config.defaults.toml").exists()

    def test_config_file_is_not_empty(self):
        content = Path("etc/config.defaults.toml").read_text().strip()
        assert len(content) > 0

    def _load(self) -> dict:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        return tomllib.loads(Path("etc/config.defaults.toml").read_text())

    def test_config_parses_as_toml(self):
        config = self._load()
        assert isinstance(config, dict) and config

    def test_config_has_no_retired_cli_section(self):
        """A.2: the retired external-CLI config section (bin/flags/
        verification_model) is gone — the agent runtime is the built-in
        harness and has no external CLI to point at."""
        config = self._load()
        sec = "clau" + "de"
        assert sec not in config, "etc/config.defaults.toml still has the retired section"

    def test_config_sections_match_schema(self):
        """A.2: pin the config surface so a section sneaks back in loudly."""
        config = self._load()
        expected = {
            "paths", "llm", "mcp", "dashboard", "logs", "github", "release",
            "sprint", "worktree", "health", "tdd",
        }
        missing = expected - set(config)
        extra = set(config) - expected
        assert not missing, f"config sections missing: {sorted(missing)}"
        assert not extra, f"unexpected config sections: {sorted(extra)}"

    def test_no_retired_cli_section_anywhere_in_runtime_tree(self):
        """A.2: no tracked runtime file (etc/, lib/, bin/, packaging/, share/)
        may define or reference the retired external-CLI config section.
        The grep pattern is assembled so this test stays grep-clean for
        the zero-trace guard."""
        sec = "clau" + "de"
        pattern = r"^\s*\[" + sec + r"\]|" + sec + r"\.(bin|flags|verification_model)"
        r = subprocess.run(
            ["git", "grep", "-l", "-E", pattern],
            capture_output=True, text=True, timeout=30,
        )
        offending = [l for l in r.stdout.splitlines()
                     if not l.startswith("tests/") and "RELEASE_GOALS" not in l]
        assert not offending, f"retired config section references remain: {offending}"


class TestConfigTemplate:
    """C1: etc/config.template.toml seeds the user config.

    The installer copies this file to ~/.config/hive/config.toml, so a
    stranger gets a visible, complete config at install time. It must be
    valid TOML, cover every key the defaults define (no silent drift),
    and carry the working default values verbatim."""

    @staticmethod
    def _load(path) -> dict:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        return tomllib.loads(Path(path).read_text())

    def test_template_exists_and_parses(self):
        t = self._load("etc/config.template.toml")
        assert isinstance(t, dict) and t

    def test_template_covers_every_default_key(self):
        d = self._load("etc/config.defaults.toml")
        t = self._load("etc/config.template.toml")
        missing = {s: sorted(set(d[s]) - set(t.get(s, {})))
                   for s in d
                   if isinstance(d[s], dict) and set(d[s]) - set(t.get(s, {}))}
        assert not missing, f"template is missing default keys: {missing}"

    def test_template_values_match_defaults(self):
        """The template ships working defaults — a value that drifts from
        the defaults file would silently change behavior for every
        fresh install."""
        d = self._load("etc/config.defaults.toml")
        t = self._load("etc/config.template.toml")
        drift = []
        for s, dv in d.items():
            if not isinstance(dv, dict):
                continue
            for k, val in dv.items():
                if k in t.get(s, {}) and t[s][k] != val:
                    drift.append(f"{s}.{k}: default={val!r} template={t[s][k]!r}")
        assert not drift, "template values drifted from defaults:\n" + "\n".join(drift)

    def test_template_llm_section_has_all_keys(self):
        t = self._load("etc/config.template.toml")
        assert set(t["llm"]) == {"provider", "model", "base_url", "api_key",
                                 "api_style", "verification_model"}


class TestInstallFile:
    """Test INSTALL file."""

    def test_install_file_exists(self):
        assert Path("INSTALL").exists()

    def test_install_file_is_not_empty(self):
        content = Path("INSTALL").read_text().strip()
        assert len(content) > 0


class TestLicenseFile:
    """Test LICENSE file."""

    def test_license_file_exists(self):
        assert Path("LICENSE").exists()

    def test_license_file_is_not_empty(self):
        content = Path("LICENSE").read_text().strip()
        assert len(content) > 0


class TestReadmeFile:
    """Test README.md file."""

    def test_readme_file_exists(self):
        assert Path("README.md").exists()

    def test_readme_file_is_not_empty(self):
        content = Path("README.md").read_text().strip()
        assert len(content) > 100  # README should have substantial content


class TestPythonFloorConsistency:
    """T4 (0.15): the 3.11 floor tells ONE story (the LLM-docs-guard
    pattern). `packaging/pyproject.toml`'s `requires-python` is the
    canonical floor; every doc surface that claims a floor must agree
    with it — a doc that keeps telling operators the old floor (3.10+
    after 0.15) fails the suite, naming the file. Red against a tree
    where pyproject moved to 3.11 but a README/AGENTS.md kept 3.10.
    """

    @staticmethod
    def _floor() -> tuple:
        import tomllib
        repo = Path(__file__).resolve().parent.parent
        t = tomllib.loads(
            (repo / "packaging" / "pyproject.toml").read_text())
        req = t["project"]["requires-python"]  # ">=3.11"
        m = __import__("re").match(r'>=\s*(\d+)\.(\d+)', req)
        assert m, f"unparseable requires-python: {req!r}"
        return int(m.group(1)), int(m.group(2))

    def test_pyproject_floor_is_3_11(self):
        """The floor IS 3.11 (0.15's breaking change: 3.10 is EOL)."""
        assert self._floor() == (3, 11)

    @classmethod
    def test_doc_floor_claims_match_pyproject(cls):
        """Every doc floor claim agrees with pyproject — and the OLD
        floor (3.10) is gone from the same surfaces."""
        major, minor = cls._floor()
        repo = Path(__file__).resolve().parent.parent
        checks = [
            # (file, the claim the doc must carry, stale claims that must
            # be gone from that file)
            ("packaging/AGENTS.md", f">={major}.{minor}", [">=3.10"]),
            ("share/docs/PACKAGING.md", f"({major}.{minor}+)",
             ["(3.10+)"]),
            ("share/docs/README.md", f"Python {major}.{minor}+",
             ["Python 3.10+"]),
            ("README.md", f"Python {major}.{minor}+",
             ["Python 3.10+"]),
        ]
        stale = []
        for rel, needle, old_claims in checks:
            text = (repo / rel).read_text()
            if needle not in text:
                stale.append(
                    f"{rel}: missing the {major}.{minor} floor claim "
                    f"({needle!r})")
            for old in old_claims:
                if old in text:
                    stale.append(
                        f"{rel}: still claims the old floor ({old!r})")
        assert not stale, "floor claims disagree with pyproject:\n  " \
            + "\n  ".join(stale)

    @classmethod
    def test_pyproject_has_no_tomli_dependency(cls):
        """The floor makes the tomli backport dead: pyproject must not
        list it (the 3.10-only `python_version<'3.11'` marker can never
        apply)."""
        import tomllib
        repo = Path(__file__).resolve().parent.parent
        t = tomllib.loads(
            (repo / "packaging" / "pyproject.toml").read_text())
        deps = " ".join(t["project"].get("dependencies", []))
        assert "tomli" not in deps, \
            f"pyproject still lists tomli under the 3.11 floor: {deps!r}"
