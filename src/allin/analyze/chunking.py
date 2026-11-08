"""Split very long transcripts into overlapping paragraph windows.

Every All-In roundtable so far is ~25-35k tokens, far under ``settings.chunk_threshold_tokens``,
so this path is a safety net. Windows break only at paragraph boundaries, keep the original
paragraph timestamps, and repeat ``settings.chunk_overlap_seconds`` of paragraphs at each
boundary so a claim straddling the cut is seen whole by at least one call.
"""

from __future__ import annotations

from ..config import Settings
from ..models import NormalizedTranscript, TranscriptParagraph
from ..util import estimate_tokens
from .prompts import mmss


def _paragraph_tokens(p: TranscriptParagraph, settings: Settings) -> int:
    # Same rendering as prompts.render_transcript: "[mm:ss] text" plus a newline.
    return estimate_tokens(f"[{mmss(p.start)}] {p.text}\n", settings.chars_per_token)


def estimate_transcript_tokens(transcript: NormalizedTranscript, settings: Settings) -> int:
    """Estimated tokens of the rendered transcript (chars / settings.chars_per_token heuristic)."""
    from .prompts import render_transcript

    return estimate_tokens(render_transcript(transcript), settings.chars_per_token)


def needs_chunking(transcript: NormalizedTranscript, settings: Settings) -> bool:
    """True when the rendered transcript exceeds ``settings.chunk_threshold_tokens``."""
    return estimate_transcript_tokens(transcript, settings) > settings.chunk_threshold_tokens


def _subset(transcript: NormalizedTranscript, paragraphs: list[TranscriptParagraph]) -> NormalizedTranscript:
    return transcript.model_copy(
        update={
            "paragraphs": list(paragraphs),
            "word_count": sum(len(p.text.split()) for p in paragraphs),
        }
    )


def chunk_transcript(transcript: NormalizedTranscript, settings: Settings) -> list[NormalizedTranscript]:
    """Split into windows of about ``settings.chunk_target_tokens`` with time-based overlap.

    A transcript that fits in one window is returned as a single chunk. Each following window
    starts at the first paragraph that begins within ``chunk_overlap_seconds`` of the previous
    window's end, but always at least one paragraph later than the previous window's start.
    """
    paras = transcript.paragraphs
    if not paras:
        return [transcript]
    sizes = [_paragraph_tokens(p, settings) for p in paras]
    target = max(1, settings.chunk_target_tokens)
    chunks: list[NormalizedTranscript] = []
    start = 0
    n = len(paras)
    while start < n:
        end = start
        total = 0
        while end < n and (end == start or total + sizes[end] <= target):
            total += sizes[end]
            end += 1
        chunks.append(_subset(transcript, paras[start:end]))
        if end >= n:
            break
        boundary = paras[end - 1].end - settings.chunk_overlap_seconds
        nxt = end
        while nxt - 1 > start and paras[nxt - 1].start >= boundary:
            nxt -= 1
        start = max(nxt, start + 1)
    return chunks
