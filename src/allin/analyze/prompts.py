"""Load the editable prompt files under ``prompts/`` and render them for one episode.

Each prompt file is plain markdown with two sections introduced by a line that is exactly
``## SYSTEM`` and a line that is exactly ``## USER``. Anything above ``## SYSTEM`` is an editor's
note and is never sent to the model. Placeholders are ``{{name}}`` and are filled with plain
string replacement (no template engine); an unknown placeholder raises ``KeyError`` so a typo in
the prompt file fails loudly instead of leaking braces to the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models import Chapter, EpisodeMeta, NormalizedTranscript

ANALYZE_PROMPT_FILE = "analyze_episode.md"
CONSOLIDATE_PROMPT_FILE = "consolidate_chunks.md"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_WS = re.compile(r"\s+")

FULL_EPISODE_NOTE = "Scope: this is the complete episode transcript."


@dataclass(frozen=True)
class PromptTemplate:
    """The two sections of a prompt file, still containing ``{{placeholders}}``."""

    system: str
    user: str
    path: str


def parse_prompt_text(text: str, source: str = "<string>") -> PromptTemplate:
    """Split a prompt file into its SYSTEM and USER sections."""
    system_lines: list[str] = []
    user_lines: list[str] = []
    current: list[str] | None = None
    seen: set[str] = set()
    for line in text.splitlines():
        marker = line.strip()
        if marker in ("## SYSTEM", "## USER"):
            name = marker[3:]
            seen.add(name)
            current = system_lines if name == "SYSTEM" else user_lines
            continue
        if current is not None:
            current.append(line)
    missing = {"SYSTEM", "USER"} - seen
    if missing:
        raise ValueError(f"{source}: missing section header(s) {sorted(missing)}; expected '## SYSTEM' and '## USER'")
    return PromptTemplate(system="\n".join(system_lines).strip(), user="\n".join(user_lines).strip(), path=source)


@lru_cache(maxsize=16)
def _load_cached(path_str: str, mtime_ns: int) -> PromptTemplate:
    text = Path(path_str).read_text(encoding="utf-8")
    return parse_prompt_text(text, source=path_str)


def load_prompt(path: Path) -> PromptTemplate:
    """Load and parse a prompt file (cached; the cache key includes mtime so edits are picked up)."""
    path = Path(path)
    return _load_cached(str(path), path.stat().st_mtime_ns)


def fill(template: str, values: dict[str, str]) -> str:
    """Replace every ``{{name}}`` in one pass, so inserted values are never re-scanned."""

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"prompt placeholder {{{{{key}}}}} has no value (known: {sorted(values)})")
        return values[key]

    return _PLACEHOLDER.sub(repl, template)


def mmss(seconds: float) -> str:
    """Format seconds as ``mm:ss`` (minutes are not wrapped into hours: 5583 s -> ``93:03``)."""
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def render_transcript(transcript: NormalizedTranscript) -> str:
    """One paragraph per line: ``[mm:ss] text`` where mm:ss is the paragraph start."""
    lines = []
    for p in transcript.paragraphs:
        text = _WS.sub(" ", p.text).strip()
        if text:
            lines.append(f"[{mmss(p.start)}] {text}")
    return "\n".join(lines)


def render_chapters(chapters: list[Chapter]) -> str:
    """One ``mm:ss title`` per line, or a short marker when the video has no chapters."""
    if not chapters:
        return "(no chapters listed)"
    return "\n".join(f"{mmss(c.start_time)} {c.title.strip()}" for c in chapters)


def render_hosts(settings: Settings) -> str:
    """Host full names (the exact strings the model must use), with the short form used on air."""
    return "\n".join(f"- {name} (called \"{settings.host_short.get(name, name)}\" on air)" for name in settings.hosts)


def _duration_minutes(meta: EpisodeMeta, transcript: NormalizedTranscript | None) -> int:
    seconds = meta.duration_seconds
    if not seconds and transcript is not None and transcript.paragraphs:
        seconds = int(transcript.paragraphs[-1].end)
    return round(seconds / 60)


def episode_values(meta: EpisodeMeta, settings: Settings, transcript: NormalizedTranscript | None = None) -> dict[str, str]:
    """Placeholder values shared by both prompts (everything except transcript-specific ones)."""
    return {
        "title": meta.title,
        "published_at": meta.published_at or "unknown",
        "duration_minutes": str(_duration_minutes(meta, transcript)),
        "chapters": render_chapters(meta.chapters),
        "hosts": render_hosts(settings),
    }


def chunk_scope_note(index: int, total: int, transcript: NormalizedTranscript) -> str:
    """Scope line for one chunk of a long transcript (index is 0-based)."""
    if not transcript.paragraphs:
        return f"Scope: part {index + 1} of {total} of the transcript (empty)."
    start = mmss(transcript.paragraphs[0].start)
    end = mmss(transcript.paragraphs[-1].end)
    return (
        f"Scope: this is part {index + 1} of {total} of the transcript ({start} to {end}). The first and "
        "last couple of minutes overlap the neighbouring parts. Extract only what is said in this part; "
        "a part may yield fewer than 8 key takes. Parts are merged afterwards."
    )


def render_analysis_prompt(
    meta: EpisodeMeta,
    transcript: NormalizedTranscript,
    settings: Settings,
    *,
    scope_note: str = FULL_EPISODE_NOTE,
) -> tuple[str, str]:
    """Return ``(system, user)`` for the per-episode (or per-chunk) analysis call."""
    tpl = load_prompt(Path(settings.prompts_dir) / ANALYZE_PROMPT_FILE)
    values = episode_values(meta, settings, transcript)
    values["scope_note"] = scope_note
    values["transcript"] = render_transcript(transcript)
    return fill(tpl.system, values), fill(tpl.user, values)


def render_consolidation_prompt(
    chunk_outputs: list[dict[str, Any]], meta: EpisodeMeta, settings: Settings
) -> tuple[str, str]:
    """Return ``(system, user)`` for merging per-chunk ``AnalysisOutput`` dicts into one."""
    tpl = load_prompt(Path(settings.prompts_dir) / CONSOLIDATE_PROMPT_FILE)
    values = episode_values(meta, settings)
    values["chunk_count"] = str(len(chunk_outputs))
    values["chunk_outputs"] = json.dumps(chunk_outputs, indent=1, ensure_ascii=False)
    return fill(tpl.system, values), fill(tpl.user, values)
