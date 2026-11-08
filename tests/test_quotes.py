"""Quote verification against the real caption fixture (offline)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from allin.analyze.quotes import locate_quote, normalize_quote, snap_timestamp, verify_quotes
from allin.models import AnalysisOutput, NormalizedTranscript, TranscriptParagraph

FIXTURE = Path(__file__).parent / "fixtures" / "youtube" / "transcript_2v8b062QzTI_head300.json"


def fixture_transcript(per_paragraph: int = 10) -> NormalizedTranscript:
    """Group the 300 real caption snippets into paragraphs (tiny stand-in for the ingest normalizer)."""
    snippets = json.loads(FIXTURE.read_text(encoding="utf-8"))
    paras = []
    for i in range(0, len(snippets), per_paragraph):
        group = snippets[i : i + per_paragraph]
        text = re.sub(r"\s+", " ", re.sub(r"\[[^\[\]]{1,40}\]", " ", " ".join(s["text"] for s in group))).strip()
        paras.append(TranscriptParagraph(start=group[0]["start"], end=group[-1]["start"] + group[-1]["duration"], text=text))
    return NormalizedTranscript(
        video_id="2v8b062QzTI", source="youtube-transcript-api", paragraphs=paras,
        word_count=sum(len(p.text.split()) for p in paras),
    )


T = fixture_transcript()


def _para_containing(fragment: str) -> int:
    return next(i for i, p in enumerate(T.paragraphs) if fragment in p.text)


def test_exact_match_verified():
    status, idx = locate_quote("It doesn't matter how good the economy actually is.", T)
    assert status == "verified"
    assert idx == _para_containing("It doesn't matter how good the economy actually is")


def test_curly_quote_and_case_variant_verified():
    status, _ = locate_quote("“It Doesn’t matter how good the economy actually is”", T)
    assert status == "verified"


def test_punctuation_differences_verified():
    # The captions say "Over a million views so far." -- model drops the period, adds a comma.
    assert locate_quote("Over a million views, so far", T)[0] == "verified"


def test_quote_spanning_paragraph_boundary_verified():
    a, b = T.paragraphs[3].text, T.paragraphs[4].text
    quote = " ".join(a.split()[-3:] + b.split()[:3])
    assert locate_quote(quote, T)[0] == "verified"


def test_slightly_altered_quote_flagged():
    status, idx = locate_quote("It does not matter how good the economy actually is", T, timestamp_seconds=517)
    assert status == "flagged"
    assert idx == _para_containing("It doesn't matter how good the economy actually is")


def test_fabricated_quote_dropped():
    assert locate_quote("Bitcoin will hit one million dollars by next Christmas", T) == ("dropped", None)
    assert locate_quote("", T) == ("none", None)
    assert locate_quote(None, T) == ("none", None)


def test_short_quote_never_fuzzy_matched():
    assert locate_quote("zebra fish", T)[0] == "dropped"


def test_normalize_quote_strips_edges():
    assert normalize_quote("  “Hello—World!” ") == "hello-world"


def test_snap_timestamp():
    idx = _para_containing("It doesn't matter how good the economy actually is")
    start = int(T.paragraphs[idx].start)
    assert snap_timestamp(start + 30, idx, T) == start + 30  # within 90 s: keep the model's value
    assert snap_timestamp(start + 400, idx, T) == start  # far off: snap to paragraph start
    assert snap_timestamp(999, None, T) == 999


def _take(quote: str, ts: int) -> dict:
    return {
        "speaker": "David Sacks", "attribution_confidence": "medium", "stance_type": "own_view",
        "summary": "s", "quote": quote, "timestamp_seconds": ts,
    }


def _pred(quote: str | None, ts: int) -> dict:
    return {
        "speaker": "David Sacks", "attribution_confidence": "medium", "stance_type": "own_view",
        "claim": "c", "category": "politics", "direction": None,
        "timeframe": {"text": "12 months", "date": None, "inferred": True},
        "resolution_criteria": "r", "stated_confidence": None, "confidence_bucket": "unstated",
        "quote": quote, "timestamp_seconds": ts,
    }


def test_verify_quotes_mutates_and_summarizes():
    out = AnalysisOutput.model_validate(
        {
            "hosts_present": [], "guests": [],
            "key_takes": [
                _take("It doesn't matter how good the economy actually is", 3000),  # verified, snapped
                _take("A sentence nobody said on this show at all", 100),  # dropped
            ],
            "predictions": [
                _pred("It does not matter how good the economy actually is", 520),  # flagged
                _pred("the robots will run Congress by 2027 for sure", 10),  # dropped -> None
                _pred(None, 10),  # none
            ],
            "stock_implications": [], "macro_themes": [], "polymarket_candidates": [],
        }
    )
    statuses, summary = verify_quotes(out, T)
    assert statuses == {
        ("key_takes", 0): "verified", ("key_takes", 1): "dropped",
        ("predictions", 0): "flagged", ("predictions", 1): "dropped", ("predictions", 2): "none",
    }
    assert (summary.checked, summary.verified, summary.flagged, summary.dropped) == (4, 1, 1, 2)
    idx = _para_containing("It doesn't matter how good the economy actually is")
    assert out.key_takes[0].timestamp_seconds == int(T.paragraphs[idx].start)
    assert out.key_takes[1].quote == "" and out.key_takes[1].summary == "s"
    assert out.predictions[0].quote is not None and out.predictions[0].timestamp_seconds == 520
    assert out.predictions[1].quote is None
