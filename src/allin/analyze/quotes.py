"""Check every model-supplied quote against the transcript text (no fabricated quotes).

Statuses (``QuoteVerificationStatus``):
- verified: the normalised quote is an exact substring of the normalised transcript (or, after
  also dropping internal punctuation, an exact word-sequence match).
- flagged: no exact match, but ``rapidfuzz.fuzz.partial_ratio`` >= 90; the quote is kept.
- dropped: neither; the quote is removed (key take quote -> "", prediction quote -> None).
- none: the model gave no quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..models import AnalysisOutput, NormalizedTranscript, QuoteVerificationStatus, QuoteVerificationSummary

FUZZY_THRESHOLD = 90.0
FUZZY_MIN_CHARS = 15  # partial_ratio on very short strings matches almost anything
WINDOW_SECONDS = 180.0
SNAP_SECONDS = 90.0

_TRANSLATE = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
        "…": "...", " ": " ",
    }
)
_CUE = re.compile(r"\[[^\[\]]{1,40}\]")
_WS = re.compile(r"\s+")
_EDGE_PUNCT = " \t\n\"'.,;:!?-()[]{}…"
_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize_text(text: str) -> str:
    """Lowercase, unify curly quotes/apostrophes/dashes, drop caption cues and ``>>``, collapse whitespace."""
    t = text.translate(_TRANSLATE).lower()
    t = _CUE.sub(" ", t).replace(">>", " ")
    return _WS.sub(" ", t).strip()


def normalize_quote(quote: str) -> str:
    """``normalize_text`` plus stripping surrounding punctuation and quote marks."""
    return normalize_text(quote).strip(_EDGE_PUNCT).strip()


def words_only(text: str) -> str:
    """Letters and digits only, single-spaced (used for the punctuation-insensitive exact match)."""
    return _WS.sub(" ", _NON_WORD.sub(" ", text)).strip()


@dataclass
class _Index:
    """Normalised transcript text with paragraph start offsets for mapping matches back."""

    text: str
    words: str
    offsets: list[int]
    word_offsets: list[int]

    @classmethod
    def build(cls, transcript: NormalizedTranscript) -> _Index:
        parts: list[str] = []
        wparts: list[str] = []
        offsets: list[int] = []
        word_offsets: list[int] = []
        pos = wpos = 0
        for p in transcript.paragraphs:
            n = normalize_text(p.text)
            w = words_only(n)
            offsets.append(pos)
            word_offsets.append(wpos)
            parts.append(n)
            wparts.append(w)
            pos += len(n) + 1
            wpos += len(w) + 1
        return cls(" ".join(parts), " ".join(wparts), offsets, word_offsets)

    @staticmethod
    def para_at(offsets: list[int], pos: int) -> int:
        lo, hi = 0, len(offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if offsets[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo


_CACHE: dict[int, tuple[NormalizedTranscript, _Index]] = {}


def _index_for(transcript: NormalizedTranscript) -> _Index:
    hit = _CACHE.get(id(transcript))
    if hit is not None and hit[0] is transcript:
        return hit[1]
    idx = _Index.build(transcript)
    _CACHE.clear()
    _CACHE[id(transcript)] = (transcript, idx)
    return idx


def _closest(candidates: list[int], transcript: NormalizedTranscript, timestamp: float | None) -> int:
    if timestamp is None or len(candidates) == 1:
        return candidates[0]
    return min(candidates, key=lambda i: abs(transcript.paragraphs[i].start - timestamp))


def _find_all(haystack: str, needle: str) -> list[int]:
    out, start = [], 0
    while (i := haystack.find(needle, start)) != -1:
        out.append(i)
        start = i + 1
    return out


def _window(transcript: NormalizedTranscript, timestamp: float, seconds: float) -> list[int]:
    return [i for i, p in enumerate(transcript.paragraphs) if p.end >= timestamp - seconds and p.start <= timestamp + seconds]


def locate_quote(
    quote: str | None,
    transcript: NormalizedTranscript,
    timestamp_seconds: float | None = None,
) -> tuple[QuoteVerificationStatus, int | None]:
    """Return ``(status, paragraph_index)`` for one quote; paragraph_index is None when dropped/none.

    When ``timestamp_seconds`` is given, multiple exact hits resolve to the nearest paragraph and
    the fuzzy search tries the paragraphs within ±180 s before the whole transcript.
    """
    if quote is None or not quote.strip():
        return "none", None
    q = normalize_quote(quote)
    if not q or not transcript.paragraphs:
        return "dropped", None
    idx = _index_for(transcript)

    hits = _find_all(idx.text, q)
    if hits:
        paras = sorted({idx.para_at(idx.offsets, h) for h in hits})
        return "verified", _closest(paras, transcript, timestamp_seconds)
    qw = words_only(q)
    if qw:
        whits = _find_all(f" {idx.words} ", f" {qw} ")
        if whits:
            paras = sorted({idx.para_at(idx.word_offsets, max(0, h)) for h in whits})
            return "verified", _closest(paras, transcript, timestamp_seconds)

    if len(q) < FUZZY_MIN_CHARS:
        return "dropped", None
    if timestamp_seconds is not None:
        win = _window(transcript, timestamp_seconds, WINDOW_SECONDS)
        if win:
            sub = " ".join(normalize_text(transcript.paragraphs[i].text) for i in win)
            al = fuzz.partial_ratio_alignment(q, sub, score_cutoff=FUZZY_THRESHOLD)
            if al is not None:
                local = _subset_offsets(transcript, win)
                return "flagged", win[_Index.para_at(local, al.dest_start)]
    al = fuzz.partial_ratio_alignment(q, idx.text, score_cutoff=FUZZY_THRESHOLD)
    if al is not None:
        return "flagged", idx.para_at(idx.offsets, al.dest_start)
    return "dropped", None


def _subset_offsets(transcript: NormalizedTranscript, indices: list[int]) -> list[int]:
    offsets, pos = [], 0
    for i in indices:
        offsets.append(pos)
        pos += len(normalize_text(transcript.paragraphs[i].text)) + 1
    return offsets


def snap_timestamp(model_ts: int, paragraph_index: int | None, transcript: NormalizedTranscript) -> int:
    """Use the matched paragraph's start when it is more than 90 s away from the model's timestamp."""
    if paragraph_index is None:
        return model_ts
    start = transcript.paragraphs[paragraph_index].start
    if abs(start - model_ts) > SNAP_SECONDS:
        return int(start)
    return model_ts


def verify_quotes(
    output: AnalysisOutput, transcript: NormalizedTranscript
) -> tuple[dict[tuple[str, int], QuoteVerificationStatus], QuoteVerificationSummary]:
    """Verify key-take and prediction quotes, mutating ``output`` in place.

    Dropped quotes are cleared (key take -> "", prediction -> None); verified quotes found more
    than 90 s from the model's ``timestamp_seconds`` snap to the matched paragraph start.
    Returns a status per ``("key_takes", i)`` / ``("predictions", i)`` and the summary counts.
    """
    statuses: dict[tuple[str, int], QuoteVerificationStatus] = {}
    summary = QuoteVerificationSummary()
    items = [("key_takes", i, t) for i, t in enumerate(output.key_takes)]
    items += [("predictions", i, p) for i, p in enumerate(output.predictions)]
    for kind, i, item in items:
        status, para = locate_quote(item.quote, transcript, item.timestamp_seconds)
        statuses[(kind, i)] = status
        if status == "none":
            continue
        summary.checked += 1
        if status == "verified":
            summary.verified += 1
            item.timestamp_seconds = snap_timestamp(item.timestamp_seconds, para, transcript)
        elif status == "flagged":
            summary.flagged += 1
        else:
            summary.dropped += 1
            item.quote = "" if kind == "key_takes" else None
    return statuses, summary
