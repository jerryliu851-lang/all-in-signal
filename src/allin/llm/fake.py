"""Deterministic backend for tests: returns queued responses, records every call."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..models import LLMUsage
from .base import LLMError, LLMResult


@dataclass
class FakeCall:
    system: str
    user: str
    schema: dict[str, Any]
    effort: str
    label: str


class FakeBackend:
    name = "fake"

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        handler: Callable[[FakeCall], dict[str, Any]] | None = None,
        model: str = "fake-model",
    ):
        self._responses = list(responses or [])
        self._handler = handler
        self.model = model
        self.calls: list[FakeCall] = []

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
        call = FakeCall(system=system, user=user, schema=schema, effort=effort, label=label)
        self.calls.append(call)
        if self._handler is not None:
            data = self._handler(call)
        elif self._responses:
            data = self._responses.pop(0)
        else:
            raise LLMError(f"FakeBackend has no response queued for {label!r}")
        usage = LLMUsage(input_tokens=len(user) // 4, output_tokens=200, cost_usd_equivalent=0.0, calls=1)
        return LLMResult(data=data, text="", usage=usage, model=model or self.model, backend=self.name, raw={})
