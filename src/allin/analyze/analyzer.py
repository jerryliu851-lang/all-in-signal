"""Run the model over one episode: a single call, or per-chunk calls plus a consolidation call.

Every call goes through ``backend.complete_json`` with the structured-output schema; the result
is then validated with ``AnalysisOutput.model_validate``. A validation failure is retried once
with the error appended to the user message, then raised.
"""

from __future__ import annotations

import sys
from typing import Any

from pydantic import ValidationError

from ..config import Settings
from ..llm import LLMBackend, LLMError
from ..models import AnalysisOutput, EpisodeMeta, LLMUsage, NormalizedTranscript
from .chunking import chunk_transcript, estimate_transcript_tokens, needs_chunking
from .prompts import chunk_scope_note, render_analysis_prompt, render_consolidation_prompt

RETRY_SUFFIX = (
    "\n\nYour previous response did not validate against the required schema:\n{error}\n"
    "Return the complete JSON object again, conforming exactly to the schema."
)


class AnalysisError(LLMError):
    """The model's output could not be validated after one retry."""


def log(msg: str) -> None:
    """Progress line to stderr (stdout is reserved for the JSON report)."""
    print(f"[analyze] {msg}", file=sys.stderr, flush=True)


def _call_validated(
    backend: LLMBackend,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    settings: Settings,
    label: str,
) -> tuple[AnalysisOutput, LLMUsage]:
    """One model call validated as ``AnalysisOutput``; one retry on validation failure."""
    usage = LLMUsage()
    prompt = user
    last_error = ""
    for attempt in (1, 2):
        result = backend.complete_json(
            system=system,
            user=prompt,
            schema=schema,
            effort=settings.analysis_effort,
            timeout_seconds=settings.llm_timeout_seconds,
            label=label,
        )
        usage = usage.add(result.usage)
        try:
            if result.data is None:
                raise ValueError("response contained no JSON object")
            return AnalysisOutput.model_validate(result.data), usage
        except (ValidationError, ValueError) as e:
            last_error = str(e)[:4000]
            log(f"{label}: output failed validation (attempt {attempt}/2): {last_error[:300]}")
            prompt = user + RETRY_SUFFIX.format(error=last_error)
    raise AnalysisError(f"{label}: output failed validation twice: {last_error[:1000]}")


def analyze_episode(
    meta: EpisodeMeta,
    transcript: NormalizedTranscript,
    backend: LLMBackend,
    settings: Settings,
    schema: dict[str, Any],
) -> tuple[AnalysisOutput, LLMUsage, bool]:
    """Analyze one episode. Returns ``(output, summed usage, chunked)``."""
    label = f"analyze:{meta.video_id}"
    tokens = estimate_transcript_tokens(transcript, settings)
    if not needs_chunking(transcript, settings):
        log(f"{meta.video_id}: single pass (~{tokens} transcript tokens)")
        system, user = render_analysis_prompt(meta, transcript, settings)
        out, usage = _call_validated(backend, system=system, user=user, schema=schema, settings=settings, label=label)
        return out, usage, False

    chunks = chunk_transcript(transcript, settings)
    log(f"{meta.video_id}: ~{tokens} transcript tokens > {settings.chunk_threshold_tokens}; {len(chunks)} chunks")
    usage = LLMUsage()
    parts: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        system, user = render_analysis_prompt(
            meta, chunk, settings, scope_note=chunk_scope_note(i, len(chunks), chunk)
        )
        out, u = _call_validated(
            backend, system=system, user=user, schema=schema, settings=settings,
            label=f"{label} chunk {i + 1}/{len(chunks)}",
        )
        usage = usage.add(u)
        parts.append(out.model_dump(mode="json"))
    system, user = render_consolidation_prompt(parts, meta, settings)
    merged, u = _call_validated(
        backend, system=system, user=user, schema=schema, settings=settings, label=f"{label} consolidate"
    )
    return merged, usage.add(u), True
