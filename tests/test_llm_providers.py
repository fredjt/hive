"""B.1/B.2: explicit LLM provider configuration + provider wire shapes."""
import json
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
