"""Check every speaker attribution against the cue the model cited for it (no unbacked "high").

The transcript has no speaker labels, so the model says who is talking, why
(``attribution_basis``), and which transcript words carry that reason (``attribution_evidence``).
This module string-matches the cue with the same matcher used for quotes and caps
``attribution_confidence`` at what the evidence supports:

- a strong basis (named handoff, addressed by name, self-reference, segment role, guest intro)
  with a cue found verbatim inside the window around the item may be ``high``;
- a strong basis whose cue is missing, only a near match, out of the window, or is just the item's
  own quote is capped at ``medium``;
- ``indirect`` is capped at ``medium``; ``topic_or_style`` / ``none`` / speaker "Unknown" at ``low``.

The check never raises a label, only lowers it. It cannot tell whether a cue really identifies the
speaker; it makes sure every ``high`` points at real words near the claim that a reader can check.
"""

from __future__ import annotations

from collections import Counter

from ..config import Settings
from ..models import (
    AnalysisOutput,
    AttributionCheckSummary,
    AttributionConfidence,
    NormalizedTranscript,
    QuoteVerificationStatus,
)
from .quotes import locate_quote, normalize_quote

STRONG_BASES = frozenset({"named_handoff", "addressed_by_name", "self_reference", "segment_role", "guest_intro"})
# For these the cue can legitimately be the claim's own words ("when I ran PayPal ...", the docket read).
SELF_EVIDENT_BASES = frozenset({"self_reference", "segment_role"})
_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_KINDS = ("key_takes", "predictions", "stock_implications")
_UNKNOWN_SPEAKERS = frozenset({"", "unknown", "unclear"})  # same spellings normalize_speaker maps to "Unknown"


def in_window(
    transcript: NormalizedTranscript, paragraph_index: int, timestamp: float, before: float, after: float
) -> bool:
    """True when the cue's paragraph overlaps ``[timestamp - before, timestamp + after]``."""
    p = transcript.paragraphs[paragraph_index]
    return p.end >= timestamp - before and p.start <= timestamp + after


def _is_own_quote(evidence: str, quote: str | None) -> bool:
    """The cited cue is just (part of) the item's supporting quote, or contains it."""
    if not quote:
        return False
    e, q = normalize_quote(evidence), normalize_quote(quote)
    return bool(e and q) and (e in q or q in e)


def cap_confidence(basis: str | None, cue_ok: bool) -> AttributionConfidence:
    """Highest confidence the cited basis and cue can support."""
    if basis in STRONG_BASES:
        return "high" if cue_ok else "medium"
    if basis == "indirect":
        return "medium"
    return "low"


def check_attributions(
    output: AnalysisOutput, transcript: NormalizedTranscript, settings: Settings
) -> tuple[dict[tuple[str, int], QuoteVerificationStatus], AttributionCheckSummary]:
    """Verify cited cues and cap confidence, mutating ``output`` in place.

    Returns the cue status per ``(kind, index)`` for key takes, predictions and stock implications
    (``verified`` means found verbatim AND inside the window) plus the summary counts. A cue that
    is not in the transcript at all is removed, like a fabricated quote.
    """
    statuses: dict[tuple[str, int], QuoteVerificationStatus] = {}
    summary = AttributionCheckSummary()
    claimed: Counter[str] = Counter()
    final: Counter[str] = Counter()
    before = float(settings.attribution_window_before_seconds)
    after = float(settings.attribution_window_after_seconds)

    for kind in _KINDS:
        for i, item in enumerate(getattr(output, kind)):
            summary.items += 1
            claimed[item.attribution_confidence] += 1
            status, para = locate_quote(item.attribution_evidence, transcript, item.timestamp_seconds)
            cue_ok = False
            if status != "none":
                summary.cited += 1
            if status == "dropped":
                summary.dropped += 1
                item.attribution_evidence = None
            elif status == "verified" and para is not None:
                if not in_window(transcript, para, item.timestamp_seconds, before, after):
                    summary.out_of_window += 1
                    status = "flagged"
                elif item.attribution_basis not in SELF_EVIDENT_BASES and _is_own_quote(
                    item.attribution_evidence or "", getattr(item, "quote", None)
                ):
                    status = "flagged"
                else:
                    summary.verified += 1
                    cue_ok = True
            statuses[(kind, i)] = status

            unknown = item.speaker.strip().lower() in _UNKNOWN_SPEAKERS
            cap = "low" if unknown else cap_confidence(item.attribution_basis, cue_ok)
            if _RANK[item.attribution_confidence] > _RANK[cap]:
                item.attribution_confidence = cap
                summary.downgraded += 1
            final[item.attribution_confidence] += 1

    summary.claimed = dict(sorted(claimed.items()))
    summary.final = dict(sorted(final.items()))
    return statuses, summary
