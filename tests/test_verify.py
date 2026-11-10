"""Runner verification: the token ping reports acceptance without ever echoing the token."""

from __future__ import annotations

import json
import types

from allin.ingest import verify as V

FAKE_TOKEN = "not-a-real-token-value-0000"


def _run(monkeypatch, stdout: str, returncode: int = 0):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return types.SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(V.subprocess, "run", fake_run)
    return seen, V.token_ping("claude")


def test_token_ping_accepted(monkeypatch):
    seen, (ok, detail) = _run(monkeypatch, json.dumps({"is_error": False, "subtype": "success", "result": "ok"}))
    assert ok and detail == "model call succeeded"
    assert seen["cmd"][:2] == ["claude", "-p"] and "haiku" in seen["cmd"]
    assert FAKE_TOKEN not in " ".join(seen["cmd"])  # the token travels in the environment only
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == FAKE_TOKEN and "CLAUDECODE" not in seen["env"]


def test_token_ping_rejected_or_garbled(monkeypatch):
    _, (ok, detail) = _run(monkeypatch, json.dumps({"is_error": True, "subtype": "success", "result": "Invalid bearer token"}), 1)
    assert not ok and detail == "rejected: Invalid bearer token" and FAKE_TOKEN not in detail
    _, (ok, detail) = _run(monkeypatch, "command not found", 127)
    assert not ok and detail.startswith("no JSON from claude -p (exit 127)") and FAKE_TOKEN not in detail
