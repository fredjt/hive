"""B.1/B.2: explicit LLM provider configuration + provider wire shapes."""
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        try:
            import tomllib
        except ImportError:  # Python 3.10 (CI matrix) — CI installs tomli
            import tomli as tomllib
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


class TestRegistryWireShapes:
    """B.2: pure protocol functions (no HTTP) for each wire dialect."""

    def _mod(self):
        if str(CORE) not in sys.path:
            sys.path.insert(0, str(CORE))
        import llm_providers
        return llm_providers

    def test_google_in_registry(self, tmp_path):
        lp = self._mod()
        assert lp.PROVIDERS["google"]["api_style"] == "gemini"
        assert lp.PROVIDERS["google"]["key_env"] == "GEMINI_API_KEY"
        _write_config(tmp_path, '[llm]\nprovider = "google"\nmodel = "gm"\n')
        r = _import_resolve()(env={"HOME": str(tmp_path), "GEMINI_API_KEY": "gk"},
                              hive_root=str(tmp_path))
        assert r.provider == "google"
        assert r.api_style == "gemini"
        assert r.api_key == "gk"
        assert r.key_from_env == "GEMINI_API_KEY"

    def test_endpoint_urls(self):
        lp = self._mod()
        assert lp.endpoint_url("http://x/v1", "openai") == "http://x/v1/chat/completions"
        assert lp.endpoint_url("http://x/v1/", "openai") == "http://x/v1/chat/completions"
        assert lp.endpoint_url("http://x", "anthropic") == "http://x/v1/messages"
        assert lp.endpoint_url("http://x/v1", "anthropic") == "http://x/v1/messages"
        assert lp.endpoint_url("http://x/v1beta", "gemini", "gm") == \
            "http://x/v1beta/models/gm:generateContent"
        assert lp.endpoint_url("http://x/v1beta", "gemini", "") == ""

    def test_headers_per_style(self):
        lp = self._mod()
        h = lp.build_headers("openai", "k")
        assert h["Authorization"] == "Bearer k"
        assert "x-api-key" not in h
        h = lp.build_headers("anthropic", "k")
        assert h["x-api-key"] == "k" and h["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in h
        h = lp.build_headers("gemini", "k")
        assert h["x-goog-api-key"] == "k"
        # no key -> no auth header, but Content-Type always present
        assert "Authorization" not in lp.build_headers("openai", None)
        assert "Content-Type" in lp.build_headers("openai", None)
        # provider attribution headers survive
        h = lp.build_headers("openai", "k", provider="openrouter")
        assert h.get("X-Title") == "Hive Agent Harness"

    def test_payload_per_style(self):
        lp = self._mod()
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "hi"}]
        p = lp.build_payload("openai", msgs, "m")
        assert p["messages"] == msgs and p["model"] == "m"
        p = lp.build_payload("anthropic", msgs, "m", max_tokens=100)
        assert p["system"] == "SYS"
        assert p["messages"] == [{"role": "user", "content": "hi"}]
        assert p["max_tokens"] == 100
        p = lp.build_payload("gemini", msgs, "m", max_tokens=100)
        assert p["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert p["systemInstruction"] == {"parts": [{"text": "SYS"}]}
        assert p["generationConfig"]["maxOutputTokens"] == 100

    def test_parse_response_per_style(self):
        lp = self._mod()
        r = lp.parse_response_body("openai", {"model": "gpt-4o",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
        assert r["ok"] and r["content"] == "hello" and r["usage"]["total_tokens"] == 3
        r = lp.parse_response_body("anthropic", {"model": "anth-model-x",
            "content": [{"type": "text", "text": "yo"}]})
        assert r["ok"] and r["content"] == "yo"
        r = lp.parse_response_body("gemini", {"modelVersion": "gm",
            "candidates": [{"content": {"parts": [{"text": "gm-hi"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2,
                              "totalTokenCount": 3}})
        assert r["ok"] and r["content"] == "gm-hi" and r["model"] == "gm"
        assert not lp.parse_response_body("gemini", {"candidates": []})["ok"]
        assert not lp.parse_response_body("openai", {"choices": []})["ok"]


class TestProviderSelection:
    def test_explicit_providers(self, tmp_path):
        expected = {
            "openai": ("openai", "https://api.openai.com/v1", True),
            "anthropic": ("anthropic", "https://api.anthropic.com", True),
            "groq": ("openai", "https://api.groq.com/openai/v1", True),
            "openrouter": ("openai", "https://openrouter.ai/api/v1", True),
            "ollama": ("openai", "http://127.0.0.1:11434/v1", False),
            "google": ("gemini", "https://generativelanguage.googleapis.com/v1beta", True),
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


class _StubLLMHandler(BaseHTTPRequestHandler):
    """Stdlib HTTP handler that records each request and returns a
    canned body; a stand-in for one provider API."""

    style = "openai"
    status = 200
    canned = b"{}"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        self.server.last_request = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }
        payload = self.canned
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_stub(style="openai", status=200, canned=b"{}"):
    """Run a stub LLM endpoint on an ephemeral port; returns
    (base_url, server). Caller must shutdown + close the server."""
    cls = type("Handler", (_StubLLMHandler,), {"style": style,
                                              "status": status,
                                              "canned": canned})
    server = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    server.last_request = None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def _load_harness():
    import importlib.util
    path = REPO / "lib" / "core" / "agent-harness.py"
    spec = importlib.util.spec_from_file_location("agent_harness_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHarnessWireShapes:
    """B.2: the harness client (call_llm_api) hits the right endpoint with
    the right auth header and payload shape for each provider wire style,
    and surfaces provider + base URL + status + truncated body on failure.
    A stdlib stub server stands in for each provider API."""

    def _call(self, style, model, key, provider=None, status=200, canned=b"{}"):
        base_url, server = _start_stub(style=style, status=status, canned=canned)
        try:
            h = _load_harness()
            result = h.call_llm_api(
                [{"role": "user", "content": "hi"}],
                api_base=base_url, model=model, api_key=key,
                api_style=style, provider=provider or style,
            )
            req = server.last_request
            assert req is not None, "stub server saw no request"
            return result, req
        finally:
            server.shutdown()
            server.server_close()

    def test_openai_style_round_trip(self):
        canned = b'{"model": "gpt-4o", "choices": [{"message": {"content": "hello"}}],'
        canned += b' "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}'
        result, req = self._call("openai", "gpt-4o", "oai-key",
                                 provider="openai", canned=canned)
        assert result["ok"] is True
        assert result["content"] == "hello"
        assert req["path"] == "/chat/completions"
        assert req["headers"].get("authorization") == "Bearer oai-key"
        body = json.loads(req["body"])
        assert body["model"] == "gpt-4o"
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    def test_anthropic_style_round_trip(self):
        canned = b'{"model": "anth-model-x", "content": [{"type": "text", "text": "yo"}]}'
        result, req = self._call("anthropic", "anth-model-x", "anth-key",
                                 provider="anthropic", canned=canned)
        assert result["ok"] is True
        assert result["content"] == "yo"
        assert req["path"] == "/v1/messages"
        assert req["headers"].get("x-api-key") == "anth-key"
        assert req["headers"].get("anthropic-version") == "2023-06-01"
        assert "authorization" not in req["headers"]
        body = json.loads(req["body"])
        assert body["max_tokens"]  # anthropic requires max_tokens

    def test_gemini_style_round_trip(self):
        canned = b'{"modelVersion": "gm", "candidates": '
        canned += b'[{"content": {"parts": [{"text": "gm-hi"}]}}]}'
        result, req = self._call("gemini", "gm", "gm-key",
                                 provider="google", canned=canned)
        assert result["ok"] is True
        assert result["content"] == "gm-hi"
        assert req["path"] == "/models/gm:generateContent"
        assert req["headers"].get("x-goog-api-key") == "gm-key"
        assert "authorization" not in req["headers"]
        body = json.loads(req["body"])
        assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        assert body["generationConfig"]["maxOutputTokens"]

    def test_error_surfaces_provider_base_status_body(self):
        canned = b'{"error": {"message": "boom from provider"}}'
        result, req = self._call("openai", "gpt-4o", "oai-key",
                                 provider="openai", status=500, canned=canned)
        assert result["ok"] is False
        err = result["error"]
        assert "provider=openai" in err
        assert "base_url=" in err
        assert "HTTP 500" in err
        assert "boom from provider" in err

    def test_client_error_reports_status(self):
        canned = b'{"error": {"message": "unauthorized"}}'
        result, req = self._call("openai", "gpt-4o", "bad-key",
                                 provider="openai", status=401, canned=canned)
        assert result["ok"] is False
        assert "HTTP 401" in result["error"]
        # client errors should not burn all retries
        assert req is not None


class TestHarnessFallbackImports:
    """Regression: the harness's path-based fallback import of the shared
    protocol modules must register them in sys.modules BEFORE exec_module.
    llm_providers uses @dataclass, and dataclasses resolves cls.__module__
    through sys.modules — without the entry the exec raises, the handler
    swallows it, and the harness silently runs the openai-only wire
    fallback (anthropic/gemini requests hit /chat/completions). The bug is
    invisible when something else has already imported llm_providers into
    the process — under xdist, whether a worker's process is cold is a
    matter of test distribution luck, so the cold case is pinned here in
    a fresh interpreter subprocess."""

    def test_cold_interpreter_loads_shared_modules(self):
        code = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('h', {path!r})\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "assert m.llm_providers is not None, 'llm_providers fallback failed'\n"
            "assert m.mcp_http is not None, 'mcp_http fallback failed'\n"
            "assert m.wire_style('anthropic', 'http://x') == 'anthropic'\n"
            "assert m.llm_providers.endpoint_url('http://x', 'anthropic', 'mm')"
            " == 'http://x/v1/messages'\n"
            "assert m.llm_providers.endpoint_url('http://x', 'gemini', 'gm')"
            " == 'http://x/models/gm:generateContent'\n"
            "print('COLD-OK')\n"
        ).format(path=str(CORE / "agent-harness.py"))
        # cwd = repo root (not lib/core): with `-I` the child has no
        # PYTHONPATH and '' -> cwd on sys.path, so lib/core is NOT
        # importable and the harness must take the path-based fallback —
        # the branch under test.
        r = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO),
        )
        assert r.returncode == 0, (
            f"cold-interpreter harness load failed: {r.stderr.strip()}")
        assert "COLD-OK" in r.stdout


class TestUnslothProvider:
    """B.3: unsloth is a named provider — operator endpoint, operator-chosen
    wire style (openai | anthropic), UNSLOTH_API_KEY env fallback."""

    def test_unsloth_openai_style_resolution(self, tmp_path):
        _write_config(tmp_path, (
            '[llm]\nprovider = "unsloth"\napi_style = "openai"\n'
            'base_url = "http://127.0.0.1:9999/v1"\nmodel = "u-model"\n'))
        r = _import_resolve()(env={"HOME": str(tmp_path),
                                   "UNSLOTH_API_KEY": "sk-unsloth"},
                              hive_root=str(tmp_path))
        assert r.provider == "unsloth"
        assert r.api_style == "openai"
        assert r.base_url == "http://127.0.0.1:9999/v1"
        assert r.api_key == "sk-unsloth"
        assert r.key_from_env == "UNSLOTH_API_KEY"
        assert r.is_configured

    def test_unsloth_anthropic_style_env_over_config(self, tmp_path):
        _write_config(tmp_path, (
            '[llm]\nprovider = "unsloth"\napi_style = "openai"\n'
            'base_url = "http://127.0.0.1:9999"\nmodel = "u-model"\n'))
        r = _import_resolve()(env={"HOME": str(tmp_path),
                                   "HIVE_API_STYLE": "anthropic",
                                   "HIVE_API_KEY": "sk-2"},
                              hive_root=str(tmp_path))
        assert r.api_style == "anthropic"
        assert r.api_key == "sk-2"  # HIVE_API_KEY outranks UNSLOTH_API_KEY

    def test_unsloth_missing_api_style_warns(self, tmp_path):
        _write_config(tmp_path, (
            '[llm]\nprovider = "unsloth"\nbase_url = "http://x"\nmodel = "m"\n'))
        r = _import_resolve()(env={"HOME": str(tmp_path),
                                   "UNSLOTH_API_KEY": "k"},
                              hive_root=str(tmp_path))
        assert any("api_style" in w for w in r.warnings)

    def test_unsloth_requires_explicit_base_url(self, tmp_path):
        _write_config(tmp_path, (
            '[llm]\nprovider = "unsloth"\napi_style = "openai"\nmodel = "m"\n'))
        r = _import_resolve()(env={"HOME": str(tmp_path),
                                   "UNSLOTH_API_KEY": "k"},
                              hive_root=str(tmp_path))
        assert not r.is_configured
        assert any("base_url" in w for w in r.warnings)

    def test_stub_round_trip_openai_style(self):
        canned = b'{"model": "u-model", "choices": '
        canned += b'[{"message": {"content": "from-unsloth"}}]}'
        base_url, server = _start_stub(canned=canned)
        try:
            h = _load_harness()
            r = h.call_llm_api(
                [{"role": "user", "content": "hi"}],
                api_base=base_url, model="u-model", api_key="sk-u",
                api_style="openai", provider="unsloth")
            assert r["ok"] and r["content"] == "from-unsloth"
            req = server.last_request
            assert req["path"] == "/chat/completions"
            assert req["headers"].get("authorization") == "Bearer sk-u"
        finally:
            server.shutdown()
            server.server_close()

    def test_stub_round_trip_anthropic_style(self):
        canned = b'{"model": "u-model", "content": '
        canned += b'[{"type": "text", "text": "anth-path"}]}'
        base_url, server = _start_stub(canned=canned)
        try:
            h = _load_harness()
            r = h.call_llm_api(
                [{"role": "user", "content": "hi"}],
                api_base=base_url, model="u-model", api_key="sk-u",
                api_style="anthropic", provider="unsloth")
            assert r["ok"] and r["content"] == "anth-path"
            req = server.last_request
            assert req["path"] == "/v1/messages"
            assert req["headers"].get("x-api-key") == "sk-u"
        finally:
            server.shutdown()
            server.server_close()

    def test_doctor_reports_unsloth_cleanly(self, tmp_path, capsys):
        code = (
            "import sys; sys.path.insert(0, 'lib');\n"
            "import os;\n"
            "os.environ['HIVE_PROVIDER']='unsloth';\n"
            "os.environ['HIVE_API_STYLE']='anthropic';\n"
            "os.environ['HIVE_API_BASE']='http://127.0.0.1:9/v1';\n"
            "os.environ['HIVE_MODEL']='u-model';\n"
            "os.environ['UNSLOTH_API_KEY']='SECRET-DO-NOT-PRINT';\n"
            "from hive_cli.commands import doctor;\n"
            "doctor.HIVE_INSTALL_DIR = __import__('pathlib').Path(os.getcwd());\n"
            "doctor.HIVE_ROOT = __import__('pathlib').Path(os.getcwd());\n"
            "import types;\n"
            "doctor.handle(types.SimpleNamespace())\n"
        )
        import subprocess
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=60, cwd=str(REPO))
        out = r.stdout
        assert "provider=unsloth" in out, out
        assert "model=u-model" in out, out
        assert "SECRET-DO-NOT-PRINT" not in out, "key leaked into doctor output"
        assert "key=env UNSLOTH_API_KEY" in out, out


class TestHarnessFailFast:
    """B.4: no provider -> exit 2 with one actionable line before any work;
    unreachable endpoint -> exit 1 naming the URL and the failure."""

    def _run(self, tmp_path, env_extra, prompt="hi", timeout=90):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")}
        env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(REPO / "lib" / "core" / "agent-harness.py"),
             "--no-mcp", "--", prompt],
            capture_output=True, text=True, timeout=timeout, env=env)

    def test_unconfigured_exits_2_with_actionable_line(self, tmp_path):
        r = self._run(tmp_path, {})
        assert r.returncode == 2, r.stderr
        assert "no LLM provider configured" in r.stderr
        assert "HIVE_PROVIDER" in r.stderr
        assert "[llm]" in r.stderr
        out = r.stdout.strip().splitlines()
        assert out, "result event expected on stdout"
        ev = json.loads(out[-1])
        assert ev["type"] == "result"
        assert ev["subtype"] == "llm-not-configured"
        assert ev["is_error"] is True

    def test_unreachable_endpoint_exits_1_with_url_and_status(self, tmp_path):
        r = self._run(tmp_path, {
            "HIVE_PROVIDER": "openai",
            "HIVE_API_BASE": "http://127.0.0.1:9",
            "HIVE_MODEL": "gpt-4o",
            "HIVE_API_KEY": "k",
        })
        assert r.returncode == 1, r.stderr
        assert "127.0.0.1:9" in r.stderr
        assert "provider=openai" in r.stderr
        out = r.stdout.strip().splitlines()
        assert out, "result event expected on stdout"
        ev = json.loads(out[-1])
        assert ev["subtype"] == "llm-unavailable"
        assert ev["is_error"] is True
        assert ev["num_turns"] == 0

    def test_cli_explicit_config_is_not_flagged_unconfigured(self, tmp_path):
        # --api-base + --model + --api-key via CLI count as configured even
        # when env/config are silent — no false fail-fast, and the call
        # actually goes out.
        canned = b'{"model": "m", "choices": [{"message": {"content": "ok"}}]}'
        base_url, server = _start_stub(canned=canned)
        try:
            env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")}
            r = subprocess.run(
                [sys.executable, str(REPO / "lib" / "core" / "agent-harness.py"),
                 "--no-mcp", "--api-base", base_url, "--model", "m",
                 "--api-key", "k", "--", "hi"],
                capture_output=True, text=True, timeout=60, env=env)
            assert r.returncode == 0, r.stderr
            assert "no LLM provider configured" not in r.stderr
            assert server.last_request is not None, "LLM call never made"
        finally:
            server.shutdown()
            server.server_close()


class TestDoctorDeep:
    """B.4: doctor's reachability probe is behind --deep; plain doctor
    stays offline-safe."""

    def _run_doctor(self, deep, extra_env):
        code = (
            "import sys; sys.path.insert(0, 'lib');\n"
            "import os;\n"
            + "".join(f"os.environ['{k}']='{v}';\n" for k, v in extra_env.items())
            + "from hive_cli.commands import doctor;\n"
            "doctor.HIVE_INSTALL_DIR = __import__('pathlib').Path(os.getcwd());\n"
            "doctor.HIVE_ROOT = __import__('pathlib').Path(os.getcwd());\n"
            "import types;\n"
            f"doctor.handle(types.SimpleNamespace(deep={deep}))\n"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=90, cwd=str(REPO))

    def test_plain_doctor_does_not_probe(self, tmp_path):
        r = self._run_doctor(False, {
            "HIVE_PROVIDER": "openai", "HIVE_API_BASE": "http://127.0.0.1:9",
            "HIVE_MODEL": "m", "HIVE_API_KEY": "k"})
        assert "LLM endpoint reachable" not in r.stdout, \
            "plain doctor must stay offline-safe"

    def test_deep_doctor_reports_unreachable(self, tmp_path):
        r = self._run_doctor(True, {
            "HIVE_PROVIDER": "openai", "HIVE_API_BASE": "http://127.0.0.1:9",
            "HIVE_MODEL": "m", "HIVE_API_KEY": "k"})
        out = r.stdout
        assert "LLM endpoint reachable (--deep)" in out, out
        assert "failed" in out
        assert "✗" in out

    def test_deep_doctor_reports_reachable(self, tmp_path):
        base_url, server = _start_stub()
        try:
            r = self._run_doctor(True, {
                "HIVE_PROVIDER": "openai", "HIVE_API_BASE": base_url,
                "HIVE_MODEL": "m", "HIVE_API_KEY": "k"})
            out = r.stdout
            assert "LLM endpoint reachable (--deep)" in out, out
            assert "TCP" in out and "reachable" in out
        finally:
            server.shutdown()
            server.server_close()


class TestVerificationModel:
    """C.3: verification_model fast path — env > config; empty = main
    model; the harness --model flag (B.1) overrides everything."""

    def test_resolve_returns_distinct_verification_model(self, tmp_path):
        _write_config(tmp_path, (
            '[llm]\nprovider = "openai"\nmodel = "main-model"\n'
            'verification_model = "verify-model"\n'))
        r = _import_resolve()(env={"HOME": str(tmp_path),
                                   "OPENAI_API_KEY": "k"},
                              hive_root=str(tmp_path))
        assert r.model == "main-model"
        assert r.verification_model == "verify-model"
        assert r.verification_model != r.model

    def test_env_wins_for_verification_model(self, tmp_path):
        _write_config(tmp_path, (
            '[llm]\nmodel = "m"\nverification_model = "config-vm"\n'))
        r = _import_resolve()(env={"HOME": str(tmp_path),
                                   "HIVE_VERIFICATION_MODEL": "env-vm"})
        assert r.verification_model == "env-vm"

    def test_verification_defaults_to_empty(self, tmp_path):
        r = _import_resolve()(env={"HOME": str(tmp_path)})
        assert r.verification_model == ""

    def test_harness_model_flag_overrides(self, tmp_path):
        canned = b'{"model": "x", "choices": [{"message": {"content": "ok"}}]}'
        base_url, server = _start_stub(canned=canned)
        try:
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(tmp_path / "home"),
                "HIVE_PROVIDER": "openai",
                "HIVE_API_BASE": base_url,
                "HIVE_MODEL": "main-model",
                "HIVE_API_KEY": "k",
            }
            r = subprocess.run(
                [sys.executable, str(REPO / "lib" / "core" / "agent-harness.py"),
                 "--no-mcp", "--model", "override-model", "--", "hi"],
                capture_output=True, text=True, timeout=60, env=env)
            assert r.returncode == 0, r.stderr
            assert server.last_request is not None
            body = json.loads(server.last_request["body"])
            assert body["model"] == "override-model"
        finally:
            server.shutdown()
            server.server_close()


class TestHarnessWithoutSiblingModules:
    """Regression (CI 2026-11-03): the container image originally shipped
    ONLY agent-harness.py, so the harness's by-name sibling imports
    (llm_providers, mcp_http) silently degraded to None inside the
    container. In that state, an env-only LLM config (HIVE_API_BASE / KEY /
    MODEL with no explicit api_style) crashed on a cold process with
    `NameError: name 'PROVIDER_CONFIGS' is not defined` — detect_provider
    still referenced the pre-B.2 registry name. Fixed: detect_provider
    reads llm_providers.PROVIDERS when available and falls back to its
    pattern list when not, and the Dockerfile now ships the siblings (see
    tests/test_dashboard.py for the image tests). These pins keep the
    harness working in the worst case (siblings absent) in a fresh
    interpreter — the same cold-process technique as TestHarnessImport
    Fallback."""

    @staticmethod
    def _isolate(tmp_path):
        """Copy the harness alone into a fresh dir — the exact layout the
        pre-fix Dockerfile produced inside the image."""
        isolated = tmp_path / "isolated" / "lib" / "core"
        isolated.mkdir(parents=True)
        shutil.copy(CORE / "agent-harness.py", isolated / "agent-harness.py")
        return isolated / "agent-harness.py"

    def test_detect_provider_without_registry(self, tmp_path):
        harness = self._isolate(tmp_path)
        code = (
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('h', {path!r})\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "assert m.llm_providers is None, 'expected sibling modules absent'\n"
            "assert m.mcp_http is None, 'expected sibling modules absent'\n"
            "assert m.detect_provider('') == 'custom'\n"
            "assert m.detect_provider('http://127.0.0.1:1/v1') == 'custom'\n"
            "assert m.detect_provider('https://api.openai.com/v1') == 'openai'\n"
            "assert m.wire_style(None, 'http://127.0.0.1:1/v1') == 'openai'\n"
            "print('ISO-OK')\n"
        ).format(path=str(harness))
        # cwd = tmp_path, -I: lib/core is NOT on sys.path, so the harness
        # cannot find its siblings — the branch under test.
        r = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
        assert r.returncode == 0, r.stderr
        assert "ISO-OK" in r.stdout

    def test_env_only_round_trip_without_registry(self, tmp_path):
        """The exact CI failure mode: env-only config (no api_style, no
        provider, no sibling modules) must still hit
        {base}/chat/completions and parse the OpenAI-style answer."""
        harness = self._isolate(tmp_path)
        canned = json.dumps({
            "id": "chatcmpl-test", "object": "chat.completion",
            "model": "stub-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "OK"},
                         "finish_reason": "stop"}],
        }).encode()
        base_url, server = _start_stub(style="openai", canned=canned)
        try:
            code = (
                "import importlib.util\n"
                "spec = importlib.util.spec_from_file_location('h', {path!r})\n"
                "m = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(m)\n"
                "r = m.call_llm_api([{{'role': 'user', 'content': 'hi'}}],\n"
                "    api_base={base!r}, model='stub-model', api_key='k')\n"
                "assert r.get('ok'), r\n"
                "print('RT-OK')\n"
            ).format(path=str(harness), base=base_url)
            r = subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
        finally:
            server.shutdown()
            server.server_close()
        assert r.returncode == 0, r.stderr
        assert "RT-OK" in r.stdout
        assert server.last_request is not None, "stub saw no request"
        assert server.last_request["path"].endswith("/chat/completions")
