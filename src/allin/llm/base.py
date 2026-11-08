"""LLM backend interface.

One production implementation: ``ClaudeCodeCLIBackend`` (Claude Code in print mode, authenticated
with a Max-subscription token). ``FakeBackend`` serves tests. An Anthropic-SDK backend would be a
third module implementing the same ``complete_json`` method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Settings
from ..models import LLMUsage


class LLMError(RuntimeError):
    """Raised when the model call fails or returns no usable structured output."""


@dataclass
class LLMResult:
    data: dict[str, Any] | None
    text: str
    usage: LLMUsage
    model: str
    backend: str
    raw: dict[str, Any] = field(default_factory=dict)


class LLMBackend(Protocol):
    name: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        effort: str = "high",
        timeout_seconds: int = 1800,
        label: str = "",
    ) -> LLMResult:
        """Run one completion that must return JSON conforming to ``schema``.

        ``label`` is free text for logs (e.g. "analyze:2v8b062QzTI"). Implementations raise
        ``LLMError`` on failure; they never return partial data silently.
        """
        ...


def get_backend(settings: Settings) -> LLMBackend:
    if settings.llm_backend == "claude-code":
        from .claude_code import ClaudeCodeCLIBackend

        return ClaudeCodeCLIBackend(settings)
    if settings.llm_backend == "fake":
        from .fake import FakeBackend

        return FakeBackend()
    raise LLMError(f"Unknown ALLIN_LLM_BACKEND={settings.llm_backend!r} (expected 'claude-code' or 'fake')")
