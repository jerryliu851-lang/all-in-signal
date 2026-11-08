"""Claude Code CLI backend: ``claude -p --output-format json --json-schema ...``.

Why this and not the Anthropic SDK: the project runs on a Max subscription token
(``claude setup-token``), which only the Claude Code CLI can use. Verified envelope fields
(Claude Code 2.1.319): ``structured_output`` (parsed object), ``result`` (text), ``is_error``,
``subtype``, ``usage`` {input_tokens, output_tokens, cache_read_input_tokens,
cache_creation_input_tokens, output_tokens_details.thinking_tokens}, ``total_cost_usd``,
``duration_ms``, ``num_turns``.

Credentials: in GitHub Actions ``CLAUDE_CODE_OAUTH_TOKEN`` must be set (from the repo secret) or
we refuse to run. Locally the logged-in CLI is used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

from ..config import Settings
from ..models import LLMUsage
from .base import LLMError, LLMResult


class ClaudeCodeCLIBackend:
    name = "claude-code"

    def __init__(self, settings: Settings, *, check_credentials: bool = True):
        self.settings = settings
        if check_credentials:
            self.require_credentials()

    # ------------------------------------------------------------------------------
    def require_credentials(self) -> None:
        if shutil.which(self.settings.claude_bin) is None:
            raise LLMError(
                f"Claude Code CLI '{self.settings.claude_bin}' not found on PATH. "
                "Install with: npm install -g @anthropic-ai/claude-code"
            )
        if self.settings.in_ci and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise LLMError(
                "CLAUDE_CODE_OAUTH_TOKEN is not set. In GitHub Actions it must come from the repository "
                "secret CLAUDE_CODE_OAUTH_TOKEN (create the token locally with `claude setup-token`). "
                "Refusing to run analysis."
            )

    # ------------------------------------------------------------------------------
    def build_command(self, *, schema: dict[str, Any], system: str, effort: str, model: str | None = None) -> list[str]:
        return [
            self.settings.claude_bin,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps({k: v for k, v in schema.items() if k != "$schema"}, separators=(",", ":")),
            "--model", model or self.settings.model,
            "--effort", effort,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--max-budget-usd", str(self.settings.max_budget_usd_per_call),
            "--system-prompt", system,
        ]

    @staticmethod
    def parse_envelope(stdout: str) -> dict[str, Any]:
        stdout = stdout.strip()
        if not stdout:
            raise LLMError("claude -p produced no output")
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Be tolerant of stray log lines before (or after) the JSON envelope.
            start = stdout.find("{")
            if start < 0:
                raise LLMError(f"claude -p output is not JSON: {stdout[:300]!r}") from None
            try:
                data, _ = json.JSONDecoder().raw_decode(stdout[start:])
            except json.JSONDecodeError as e:
                raise LLMError(f"claude -p output is not valid JSON ({e}): {stdout[:300]!r}") from None
        if isinstance(data, list):
            results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
            if not results:
                raise LLMError("claude -p returned a list without a result envelope")
            data = results[-1]
        if not isinstance(data, dict):
            raise LLMError("claude -p envelope is not an object")
        return data

    @staticmethod
    def usage_from_envelope(env: dict[str, Any]) -> LLMUsage:
        u = env.get("usage") or {}
        details = u.get("output_tokens_details") or {}
        return LLMUsage(
            input_tokens=int(u.get("input_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0),
            cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
            thinking_tokens=int(details.get("thinking_tokens") or 0),
            cost_usd_equivalent=float(env.get("total_cost_usd") or 0.0),
            duration_ms=int(env.get("duration_ms") or 0),
            calls=1,
        )

    @staticmethod
    def extract_data(env: dict[str, Any]) -> dict[str, Any] | None:
        data = env.get("structured_output")
        if isinstance(data, dict):
            return data
        text = env.get("result")
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    # ------------------------------------------------------------------------------
    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        effort: str = "high",
        timeout_seconds: int = 1800,
        label: str = "",
        model: str | None = None,
    ) -> LLMResult:
        cmd = self.build_command(schema=schema, system=system, effort=effort, model=model)
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)  # allow running from inside another Claude Code session
        print(f"[llm] claude -p model={model or self.settings.model} effort={effort} {label}", file=sys.stderr, flush=True)
        try:
            proc = subprocess.run(
                cmd, input=user, capture_output=True, text=True, timeout=timeout_seconds, env=env, check=False
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"claude -p timed out after {timeout_seconds}s ({label})") from e

        if proc.returncode != 0 and not proc.stdout.strip():
            raise LLMError(f"claude -p exited {proc.returncode} ({label}): {proc.stderr.strip()[-2000:]}")

        envelope = self.parse_envelope(proc.stdout)
        if envelope.get("is_error") or envelope.get("subtype") not in (None, "success"):
            raise LLMError(
                f"claude -p reported an error ({label}): subtype={envelope.get('subtype')} "
                f"result={str(envelope.get('result'))[:500]!r} stderr={proc.stderr.strip()[-800:]!r}"
            )
        data = self.extract_data(envelope)
        if data is None:
            raise LLMError(f"claude -p returned no structured output ({label}): {str(envelope.get('result'))[:500]!r}")
        usage = self.usage_from_envelope(envelope)
        print(
            f"[llm] done {label}: in={usage.input_tokens} cache_read={usage.cache_read_input_tokens} "
            f"cache_create={usage.cache_creation_input_tokens} out={usage.output_tokens} "
            f"think={usage.thinking_tokens} cost_equiv=${usage.cost_usd_equivalent:.4f} {usage.duration_ms}ms",
            file=sys.stderr,
            flush=True,
        )
        return LLMResult(
            data=data,
            text=str(envelope.get("result") or ""),
            usage=usage,
            model=model or self.settings.model,
            backend=self.name,
            raw=envelope,
        )
