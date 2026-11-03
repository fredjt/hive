"""B.1: explicit LLM provider configuration."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "lib" / "core"


def _import_resolve():
    if str(CORE) not in sys.path:
        sys.path.insert(0, str(CORE))
    import llm_providers
    return llm_providers.resolve

def _write_config(tmp_path, text):
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc" / "config.defaults.toml").write_text(text)



class TestLlmConfigSection:
    def test_defaults_have_llm_section(self):
        import tomllib
        config = tomllib.loads((REPO / "etc" / "config.defaults.toml").read_text())
        assert "llm" in config
        llm = config["llm"]
        for key in ("provider", "model", "base_url", "api_key", "api_style",
                    "verification_model"):
            assert key in llm, f"[llm] missing key: {key}"


class TestResolutionPrecedence:
    def test_env_wins_over_config(self, tmp_path):
        _write_config(tmp_path, 
            "[llm]\nprovider = \"anthropic\"\nmodel = \"cfg-model\"\n")
        env = {
            "HOME": str(tmp_path),
            "HIVE_PROVIDER": "openai",
            "HIVE_MODEL": "env-model",
            "HIVE_API_KEY": "test-key-12345",
        }
        r = _import_resolve()(env=env, hive_root=str(tmp_path))
        assert r.provider == "openai"
        assert r.model == "env-model"
        assert r.api_key == "test-key-12345"
        assert r.key_from_env == "HIVE_API_KEY"

    def test_config_wins_over_provider_defaults(self, tmp_path):
        _write_config(tmp_path, 
            "[llm]\nprovider = \"groq\"\nmodel = \"cfg-model\"\n")
        r = _import_resolve()(env={"HOME": str(tmp_path)}, hive_root=str(tmp_path))
        assert r.provider == "groq"
        assert r.model == "cfg-model"
        assert r.base_url == "https://api.groq.com/openai/v1"

    def test_env_api_key_over_config_key(self, tmp_path):
        _write_config(tmp_path, 
            "[llm]\nprovider = \"openai\"\napi_key = \"cfg-key\"\n")
        r = _import_resolve()(
            env={"HOME": str(tmp_path), "HIVE_API_KEY": "env-key"},
            hive_root=str(tmp_path))
        assert r.api_key == "env-key"

    def test_provider_env_key_fallback(self, tmp_path):
        r = _import_resolve()(
            env={"HOME": str(tmp_path), "OPENAI_API_KEY": "oai-fallback"},
            hive_root=str(tmp_path))
        # no provider set, no base url -> unconfigured
        assert not r.is_configured
        _write_config(tmp_path, 
            "[llm]\nprovider = \"openai\"\n")
        r = _import_resolve()(
            env={"HOME": str(tmp_path), "OPENAI_API_KEY": "oai-fallback"},
            hive_root=str(tmp_path))
        assert r.api_key == "oai-fallback"
        assert r.key_from_env == "OPENAI_API_KEY"


class TestProviderSelection:
    def test_explicit_providers(self, tmp_path):
        expected = {
            "openai": ("openai", "https://api.openai.com/v1", True),
            "anthropic": ("anthropic", "https://api.anthropic.com", True),
            "groq": ("openai", "https://api.groq.com/openai/v1", True),
            "openrouter": ("openai", "https://openrouter.ai/api/v1", True),
            "ollama": ("openai", "http://127.0.0.1:11434/v1", False),
        }
        for name, (style, base, needs_key) in expected.items():
            _write_config(tmp_path, 
                f"[llm]\nprovider = \"{name}\"\n")
            r = _import_resolve()(env={"HOME": str(tmp_path)},
                                  hive_root=str(tmp_path))
            assert r.provider == name, name
            assert r.api_style == style, name
            assert r.base_url == base, name
            assert r.needs_key is needs_key, name

    def test_unknown_provider_is_tolerated(self, tmp_path):
        r = _import_resolve()(
            env={"HOME": str(tmp_path), "HIVE_PROVIDER": "bogus"},
            hive_root=str(tmp_path))
        assert r.provider == "bogus"
        assert r.api_style == "openai"
        assert any("Unknown provider" in w for w in r.warnings)

    def test_detection_fallback_warns(self, tmp_path):
        r = _import_resolve()(
            env={"HOME": str(tmp_path),
                 "HIVE_API_BASE": "http://127.0.0.1:11434/v1",
                 "HIVE_MODEL": "llama3"},
            hive_root=str(tmp_path))
        assert r.provider == "ollama"
        assert any("detected" in w for w in r.warnings)

    def test_unconfigured(self, tmp_path):
        r = _import_resolve()(env={"HOME": str(tmp_path)},
                              hive_root=str(tmp_path))
        assert not r.is_configured
        assert any("No LLM provider" in w for w in r.warnings)


class TestDoctorReportsProvider:
    def test_doctor_reports_provider_and_hides_key(self, tmp_path, capsys):
        # doctor reads llm config from the repo; feed it via env so the
        # resolved provider/model surface, and confirm the key value never
        # appears in the output.
        import importlib
        code = (
            "import sys; sys.path.insert(0, 'lib');\n"
            "import os;\n"
            "os.environ['HIVE_PROVIDER']='openai';\n"
            "os.environ['HIVE_MODEL']='env-model';\n"
            "os.environ['HIVE_API_KEY']='SECRET-DO-NOT-PRINT';\n"
            "from hive_cli.commands import doctor;\n"
            "doctor.HIVE_INSTALL_DIR = __import__('pathlib').Path(os.getcwd());\n"
            "doctor.HIVE_ROOT = __import__('pathlib').Path(os.getcwd());\n"
            "import types;\n"
            "doctor.handle(types.SimpleNamespace())\n"
        )
        import subprocess
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60, cwd=str(REPO),
        )
        out = r.stdout
        assert "LLM provider" in out, out
        assert "provider=openai" in out, out
        assert "model=env-model" in out, out
        assert "SECRET-DO-NOT-PRINT" not in out, "key leaked into doctor output"
        assert "key=env HIVE_API_KEY" in out, out
