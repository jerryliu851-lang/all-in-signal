"""Attribution cue check: a "high" must point at real transcript words near the item."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from allin.analyze.attribution import STRONG_BASES, cap_confidence, check_attributions, in_window
from allin.analyze.prompts import CAPTION_NOTES, WHISPER_NOTES, render_analysis_prompt, transcript_notes
from allin.config import Settings
from allin.ingest.normalize import normalize
from allin.models import (
    AnalysisOutput,
    EpisodeAnalysis,
    EpisodeMeta,
    KeyTake,
    Prediction,
    StockImplication,
    TranscriptSnippet,
)
from allin.schema import analysis_output_schema

FIX = Path(__file__).parent / "fixtures"
VID = "2v8b062QzTI"
SETTINGS = Settings()

# Real lines from the fixture transcript (first ~11 minutes of the 2026-11-07 episode).
HANDOFF = "Sax people want to hear from you most of all"  # paragraph starting at 269 s
OPENING = "Welcome back to the number one podcast in the world"  # paragraph starting at 0 s
SACKS_CLAIM = "This was a blowout"  # paragraph starting at 294 s


def transcript(source: str = "youtube-transcript-api"):
    raw = json.loads((FIX / "youtube" / f"transcript_{VID}_head300.json").read_text())
    return normalize(VID, [TranscriptSnippet(**r) for r in raw], source, True)


T = transcript()


def take(*, speaker="David Sacks", basis="named_handoff", evidence=HANDOFF, confidence="high",
         quote=SACKS_CLAIM, ts=294) -> dict:
    return {
        "speaker": speaker, "attribution_basis": basis, "attribution_evidence": evidence,
        "attribution_confidence": confidence, "stance_type": "own_view", "summary": "s", "quote": quote,
        "timestamp_seconds": ts,
    }


def pred(**kw) -> dict:
    t = take(**kw)
    t.pop("summary")
    return {
        **t, "claim": "c", "category": "politics", "direction": None,
        "timeframe": {"text": "12 months", "date": None, "inferred": True},
        "resolution_criteria": "r", "stated_confidence": None, "confidence_bucket": "unstated",
    }


def stock(*, speaker="David Sacks", basis="named_handoff", evidence=HANDOFF, confidence="high", ts=294) -> dict:
    return {
        "claim": "c", "tickers": ["XLE"], "company": None, "is_private": False, "sector": "energy",
        "direction": "bullish", "timeframe": "12 months", "rationale": "r", "speaker": speaker,
        "attribution_basis": basis, "attribution_evidence": evidence, "attribution_confidence": confidence,
        "timestamp_seconds": ts, "note": None,
    }


def output(*, takes=(), preds=(), stocks=()) -> AnalysisOutput:
    return AnalysisOutput.model_validate({
        "hosts_present": ["David Sacks", "Jason Calacanis"], "guests": [],
        "key_takes": list(takes), "predictions": list(preds), "stock_implications": list(stocks),
        "macro_themes": [], "polymarket_candidates": [],
    })


def test_cap_confidence_table():
    for basis in STRONG_BASES:
        assert cap_confidence(basis, True) == "high"
        assert cap_confidence(basis, False) == "medium"
    assert cap_confidence("indirect", True) == "medium"
    assert cap_confidence("topic_or_style", True) == "low"
    assert cap_confidence("none", False) == "low"
    assert cap_confidence(None, True) == "low"  # legacy item without a basis


def test_in_window_bounds():
    first = 0  # paragraph 0 spans 0-36 s
    assert in_window(T, first, 400, 480, 120)
    assert not in_window(T, first, 600, 480, 120)  # 600 - 480 = 120 > paragraph end
    last = len(T.paragraphs) - 1  # starts at 640 s
    assert in_window(T, last, 530, 480, 120)
    assert not in_window(T, last, 500, 480, 120)


def test_verified_cue_in_window_keeps_high():
    out = output(takes=[take()], preds=[pred()], stocks=[stock()])
    statuses, summary = check_attributions(out, T, SETTINGS)
    assert set(statuses.values()) == {"verified"}
    assert [x.attribution_confidence for x in (*out.key_takes, *out.predictions, *out.stock_implications)] == ["high"] * 3
    assert summary.model_dump() == {
        "items": 3, "cited": 3, "verified": 3, "out_of_window": 0, "dropped": 0, "downgraded": 0,
        "claimed": {"high": 3}, "final": {"high": 3},
    }


def test_fabricated_cue_is_removed_and_capped():
    out = output(preds=[pred(evidence="Sacks, give us your hottest take on the midterms")])
    statuses, summary = check_attributions(out, T, SETTINGS)
    p = out.predictions[0]
    assert statuses[("predictions", 0)] == "dropped"
    assert p.attribution_evidence is None and p.attribution_confidence == "medium"
    assert (summary.cited, summary.dropped, summary.downgraded) == (1, 1, 1)
    assert summary.claimed == {"high": 1} and summary.final == {"medium": 1}


def test_missing_cue_caps_strong_basis_at_medium():
    out = output(takes=[take(evidence=None)])
    statuses, summary = check_attributions(out, T, SETTINGS)
    assert statuses[("key_takes", 0)] == "none"
    assert out.key_takes[0].attribution_confidence == "medium"
    assert (summary.cited, summary.downgraded) == (0, 1)


def test_cue_outside_window_does_not_support_high():
    # The opening line is real, but an item at 640 s is more than the 480 s window after it.
    out = output(takes=[take(speaker="Jason Calacanis", basis="segment_role", evidence=OPENING, quote="", ts=640)])
    statuses, summary = check_attributions(out, T, SETTINGS)
    assert statuses[("key_takes", 0)] == "flagged"
    assert out.key_takes[0].attribution_confidence == "medium"
    assert out.key_takes[0].attribution_evidence == OPENING  # real words are kept, just not counted
    assert (summary.out_of_window, summary.verified, summary.downgraded) == (1, 0, 1)


def test_weak_bases_are_capped_whatever_the_model_claims():
    out = output(preds=[
        pred(basis="indirect", confidence="high"),
        pred(basis="topic_or_style", evidence=None, confidence="medium"),
        pred(basis="none", evidence=None, confidence="high", speaker="Unknown"),
    ])
    _, summary = check_attributions(out, T, SETTINGS)
    assert [p.attribution_confidence for p in out.predictions] == ["medium", "low", "low"]
    assert summary.downgraded == 3


def test_unknown_speaker_is_always_low():
    out = output(takes=[take(speaker="Unknown", confidence="high")])
    check_attributions(out, T, SETTINGS)
    assert out.key_takes[0].attribution_confidence == "low"


def test_own_quote_is_not_a_cue_unless_self_evident():
    out = output(takes=[
        take(evidence=SACKS_CLAIM, quote=SACKS_CLAIM),  # named_handoff citing the claim itself
        take(basis="self_reference", evidence=SACKS_CLAIM, quote=SACKS_CLAIM),
    ])
    statuses, _ = check_attributions(out, T, SETTINGS)
    assert statuses[("key_takes", 0)] == "flagged" and out.key_takes[0].attribution_confidence == "medium"
    assert statuses[("key_takes", 1)] == "verified" and out.key_takes[1].attribution_confidence == "high"


def test_check_never_raises_a_label():
    out = output(takes=[take(confidence="low"), take(confidence="medium")])
    _, summary = check_attributions(out, T, SETTINGS)
    assert [t.attribution_confidence for t in out.key_takes] == ["low", "medium"]
    assert summary.downgraded == 0 and summary.verified == 2


def test_stored_models_accept_analyses_made_before_cues_existed():
    legacy = {"speaker": "David Sacks", "attribution_confidence": "high", "stance_type": "own_view",
              "summary": "s", "quote": "q", "timestamp_seconds": 1}
    k = KeyTake.model_validate(legacy)
    assert k.attribution_basis is None and k.attribution_evidence is None
    assert k.attribution_evidence_verified == "none"
    for cls in (KeyTake, Prediction, StockImplication):
        assert not cls.model_fields["attribution_basis"].is_required()
    meta = EpisodeMeta(video_id=VID, title="t", published_at="2026-11-07", url="u", duration_seconds=1)
    a = EpisodeAnalysis(episode=meta, hosts_present=[], guests=[], key_takes=[k], predictions=[],
                        stock_implications=[], macro_themes=[], polymarket_candidates=[],
                        analyzed_at="2026-11-08T00:00:00Z", model="m", backend="b")
    assert a.attribution_check is None


def test_model_output_must_carry_basis_and_evidence():
    schema = analysis_output_schema()
    for name in ("KeyTakeOut", "PredictionOut", "StockImplicationOut"):
        d = schema["$defs"][name]
        assert {"attribution_basis", "attribution_evidence", "attribution_confidence"} <= set(d["required"])
        order = list(d["properties"])
        assert order.index("attribution_basis") < order.index("attribution_evidence") < order.index("attribution_confidence")
    legacy = take()
    legacy.pop("attribution_basis")
    with pytest.raises(ValueError):
        output(takes=[legacy])


def test_prompt_describes_the_transcript_source():
    meta = EpisodeMeta(video_id=VID, title="t", published_at="2026-11-07", url="u", duration_seconds=682)
    captions, whisper = transcript(), transcript("podcast-whisper:small.en")
    assert transcript_notes(captions) == CAPTION_NOTES and transcript_notes(whisper) == WHISPER_NOTES
    sys_c, _ = render_analysis_prompt(meta, captions, SETTINGS)
    sys_w, _ = render_analysis_prompt(meta, whisper, SETTINGS)
    assert "`>>` inside a line usually marks a change of speaker" in sys_c
    assert "NO speaker-change markers" in sys_w and "`>>` inside a line" not in sys_w
    for system in (sys_c, sys_w):
        assert "{{" not in system
        for basis in (*STRONG_BASES, "indirect", "topic_or_style"):
            assert basis in system  # the prompt and the cap table name the same bases
