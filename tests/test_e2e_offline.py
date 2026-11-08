"""Offline end-to-end: transcript -> run_analyze -> run_match -> run_resolve (fake model, mocked Gamma)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from allin.analyze.pipeline import run_analyze
from allin.ingest.normalize import normalize
from allin.llm.fake import FakeBackend, FakeCall
from allin.models import Chapter, IndexEntry, TranscriptSnippet
from allin.polymarket.gamma import GammaClient
from allin.polymarket.pipeline import run_match
from allin.scoring.pipeline import run_resolve

FIX = Path(__file__).parent / "fixtures"
VID = "2v8b062QzTI"


def _transcript():
    raw = json.loads((FIX / "youtube" / "transcript_2v8b062QzTI_head300.json").read_text())
    return normalize(VID, [TranscriptSnippet(**r) for r in raw], "youtube-transcript-api", True)


def _pred(speaker, stance, claim, quote, ts, date):
    return {
        "speaker": speaker, "attribution_confidence": "medium", "stance_type": stance, "claim": claim,
        "category": "politics", "direction": "yes",
        "timeframe": {"text": "by the date", "date": date, "inferred": False},
        "resolution_criteria": "Official result.", "stated_confidence": None, "confidence_bucket": "high",
        "quote": quote, "timestamp_seconds": ts,
    }


def _analysis_output():
    return {
        "hosts_present": ["Jason", "David Sacks"],
        "guests": [],
        "key_takes": [
            {"speaker": "Sacks", "attribution_confidence": "high", "stance_type": "own_view",
             "summary": "Dems gain Senate seats.", "quote": "Dems will wind up with 54, 55 or 56 seats",
             "timestamp_seconds": 231},
            {"speaker": "Jason Calacanis", "attribution_confidence": "low", "stance_type": "own_view",
             "summary": "Fabricated.", "quote": "the moon is made of green cheese and always was",
             "timestamp_seconds": 10},
        ],
        "predictions": [
            _pred("David Sacks", "own_view", "Dems hold 54+ Senate seats.",
                  "Dems will wind up with 54, 55 or 56 seats", 231, "2026-01-01"),
            _pred("Chamath", "reported_view", "Something reported.", "this quote was never said on the show", 40,
                  "2027-06-30"),
        ],
        "stock_implications": [],
        "macro_themes": [{"theme": " Elections ", "stance_summary": "x", "speakers": ["Jason"], "timestamp_seconds": 230}],
        "polymarket_candidates": [
            {"prediction_index": 0, "question": "Fed rate cut by January 2026 meeting?", "hosts_answer": "yes",
             "implied_probability": 0.8},
            {"prediction_index": 1, "question": "Will something reported happen?", "hosts_answer": "no",
             "implied_probability": 0.3},
        ],
    }


def test_offline_end_to_end(tmp_store, settings):
    tmp_store.save_normalized_transcript(_transcript())
    index = tmp_store.load_index()
    index.episodes[VID] = IndexEntry(
        video_id=VID, title="Midterm Blood Bath", published_at="2026-11-07", url=f"https://www.youtube.com/watch?v={VID}",
        duration_seconds=5584, status="transcribed", discovered_at="2026-11-07T00:00:00Z",
        chapters=[Chapter(start_time=0, title="Intro")],
    )
    tmp_store.save_index(index)

    rep = run_analyze(tmp_store, settings, FakeBackend([_analysis_output()]))
    assert rep.analyzed == [VID] and not rep.failed
    a = tmp_store.load_analysis(VID)
    assert tmp_store.load_index().episodes[VID].status == "analyzed"
    assert [p.id for p in a.predictions] == [f"{VID}-p01", f"{VID}-p02"]
    assert [c.prediction_id for c in a.polymarket_candidates] == [f"{VID}-p01", f"{VID}-p02"]
    assert a.key_takes[0].speaker == "David Sacks" and a.key_takes[0].quote_verified == "verified"
    assert a.key_takes[1].quote_verified == "dropped" and a.key_takes[1].quote == ""
    assert a.predictions[1].speaker == "Chamath Palihapitiya" and a.predictions[1].quote is None
    assert a.quote_verification.dropped == 2 and a.quote_verification.verified == 2
    assert a.macro_themes[0].theme == "elections"
    # nothing left to analyze: a second run is a clean no-op
    assert run_analyze(tmp_store, settings, FakeBackend([])).analyzed == []

    search = json.loads((FIX / "gamma" / "public_search_fed.json").read_text())

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/public-search":
            return httpx.Response(200, json=search)
        return httpx.Response(404)

    gamma = GammaClient(base_url="https://gamma.test", client=httpx.Client(transport=httpx.MockTransport(handler)),
                        sleep=lambda s: None)

    def judge(call: FakeCall) -> dict:
        assert "match_market" not in call.system
        if call.label.endswith("-p01"):
            return {"match": True, "market_id": "949492", "confidence": "high", "polarity": "same", "rationale": "same"}
        return {"match": False, "market_id": None, "confidence": "low", "polarity": "n/a", "rationale": "none"}

    mrep = run_match(tmp_store, settings, FakeBackend(handler=judge), gamma=gamma)
    assert mrep.matched == [f"{VID}-p01"]
    assert mrep.unmatched == [f"{VID}-p02"]
    # 949492 is closed and resolved No; the hosts said yes with polarity same -> incorrect
    assert mrep.resolved_from_closed == [f"{VID}-p01"]

    rrep = run_resolve(tmp_store, settings)
    assert rrep.still_pending == 1
    ledger = tmp_store.load_ledger()
    assert ledger.count == 2
    by_id = {e.id: e for e in ledger.predictions}
    p1 = by_id[f"{VID}-p01"]
    assert p1.status == "resolved_incorrect" and p1.resolution_source == "polymarket"
    assert p1.polymarket_question == "Fed rate cut by January 2026 meeting?"
    assert p1.polymarket_yes_probability == 0.0 and p1.polymarket_divergence_points == 80.0
    assert p1.youtube_timestamp_url.endswith("&t=231s")
    assert by_id[f"{VID}-p02"].timeframe_passed is False
    card = tmp_store.load_scorecard()
    sacks = card.hosts["David Sacks"].overall
    assert (sacks.total, sacks.resolved, sacks.incorrect, sacks.accuracy) == (1, 1, 1, 0.0)
    # reported_view is excluded from the scorecard entirely
    assert card.hosts["Chamath Palihapitiya"].overall.total == 0 and card.others == {}
    # resolve is idempotent
    assert run_resolve(tmp_store, settings).resolved == []
