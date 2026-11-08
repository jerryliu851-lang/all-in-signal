from __future__ import annotations

import json
import subprocess

import pytest

from allin.config import Settings
from allin.llm import LLMError
from allin.llm.claude_code import ClaudeCodeCLIBackend
from allin.llm.fake import FakeBackend


def test_parse_real_envelope(load_fixture):
    env = load_fixture("claude_envelope.json")
    data = ClaudeCodeCLIBackend.extract_data(env)
    assert data == {"answer": "pong"}
    usage = ClaudeCodeCLIBackend.usage_from_envelope(env)
    assert usage.cache_creation_input_tokens == 11742
    assert usage.output_tokens == 53
    assert usage.calls == 1
    assert usage.cost_usd_equivalent > 0


def test_parse_tolerates_log_prefix():
    env = ClaudeCodeCLIBackend.parse_envelope('warning: something\n{"type":"result","is_error":false,"result":"{}"}')
    assert env["type"] == "result"


def test_complete_json_runs_cli(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path, claude_bin="claude")
    backend = ClaudeCodeCLIBackend(settings, check_credentials=False)
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw["input"]
        envelope = {"type": "result", "subtype": "success", "is_error": False, "result": "{\"a\":1}",
                    "structured_output": {"a": 1}, "usage": {"input_tokens": 10, "output_tokens": 5},
                    "total_cost_usd": 0.01, "duration_ms": 5}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = backend.complete_json(system="sys", user="hello", schema={"type": "object"}, effort="low", label="t")
    assert res.data == {"a": 1}
    assert captured["input"] == "hello"
    assert "--json-schema" in captured["cmd"] and "--model" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "claude-opus-5-5"
    assert captured["cmd"][captured["cmd"].index("--effort") + 1] == "low"


def test_complete_json_raises_on_error_envelope(monkeypatch, tmp_path):
    backend = ClaudeCodeCLIBackend(Settings(data_dir=tmp_path), check_credentials=False)

    def fake_run(cmd, **kw):
        envelope = {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}
        return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps(envelope), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(LLMError):
        backend.complete_json(system="s", user="u", schema={"type": "object"})


def test_ci_requires_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    with pytest.raises(LLMError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        ClaudeCodeCLIBackend(Settings(data_dir=tmp_path))


def test_fake_backend_queue():
    fb = FakeBackend(responses=[{"x": 1}])
    r = fb.complete_json(system="s", user="u", schema={})
    assert r.data == {"x": 1} and len(fb.calls) == 1
    with pytest.raises(LLMError):
        fb.complete_json(system="s", user="u", schema={})
