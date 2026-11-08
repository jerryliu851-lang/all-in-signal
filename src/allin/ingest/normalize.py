"""Merge raw caption fragments into clean, timestamped paragraphs (deterministic, keeps every word)."""

from __future__ import annotations

import html
import re

from ..models import NormalizedTranscript, TranscriptParagraph, TranscriptSnippet

_CUE = re.compile(r"\[[^\[\]]{1,40}\]")
_WS = re.compile(r"\s+")
_SENT_END = re.compile(r"""[.?!]["'”’)\]]*$""")
_SOFT_WORDS = 60


def clean_text(text: str) -> str:
    """Decode (double-encoded) HTML entities, drop [cue] tags, join hyphenated line breaks, collapse whitespace."""
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _CUE.sub(" ", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return _WS.sub(" ", text).strip()


def normalize(
    video_id: str,
    snippets: list[TranscriptSnippet],
    source: str,
    is_generated: bool,
    *,
    max_paragraph_seconds: int = 45,
    max_paragraph_words: int = 120,
) -> NormalizedTranscript:
    """Build paragraphs: soft break at sentence ends past ~60 words, hard break at the caps."""
    paragraphs: list[TranscriptParagraph] = []
    parts: list[str] = []
    words = 0
    p_start = 0.0
    p_end = 0.0

    def flush() -> None:
        nonlocal parts, words
        if parts:
            paragraphs.append(TranscriptParagraph(start=p_start, end=p_end, text=" ".join(parts)))
        parts, words = [], 0

    for sn in snippets:
        text = clean_text(sn.text)
        if not text:
            continue
        n = len(text.split())
        end = sn.start + sn.duration
        if parts and (words + n > max_paragraph_words or end - p_start > max_paragraph_seconds):
            flush()
        if not parts:
            p_start = sn.start
        if parts and parts[-1].endswith("-") and not parts[-1].endswith("--") and text[:1].islower():
            parts[-1] = parts[-1][:-1] + text
        else:
            parts.append(text)
        words += n
        p_end = end
        if words >= _SOFT_WORDS and _SENT_END.search(text):
            flush()
    flush()
    total = sum(len(p.text.split()) for p in paragraphs)
    return NormalizedTranscript(
        video_id=video_id, source=source, is_generated=is_generated, paragraphs=paragraphs, word_count=total
    )
