"""End-to-end analyze pipeline and estimate on a tmp store (offline, FakeBackend)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from allin.analyze.estimate import estimate_episodes
from allin.analyze.pipeline import make_ids, normalize_speaker, run_analyze
from allin.analyze.prompts import render_analysis_prompt
from allin.config import PRICE_PER_MTOK, Settings
from allin.llm.fake import FakeBackend
from allin.models import Chapter, IndexEntry, NormalizedTranscript, TranscriptParagraph
from allin.util import estimate_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "youtube" / "transcript_2v8b062QzTI_head300.json"
VID = "2v8b062QzTI"


def fixture_transcript(video_id: str = VID, per_paragraph: int = 10) -> NormalizedTranscript:
    snippets = json.loads(FIXTURE.read_text(encoding="utf-8"))
    paras = []
    for i in range(0, len(snippets), per_paragraph):
        group = snippets[i : i + per_paragraph]
        text = re.sub(r"\s+", " ", re.sub(r"\[[^\[\]]{1,40}\]", " ", " ".join(s["text"] for s in group))).strip()
        paras.append(TranscriptParagraph(start=group[0]["start"], end=group[-1]["start"] + group[-1]["duration"], text=text))
    return NormalizedTranscript(
        video_id=video_id, source="youtube-transcript-api", paragraphs=paras,
        word_count=sum(len(p.text.split()) for p in paras),
    )


def entry(video_id: str = VID, status: str = "transcribed") -> IndexEntry:
    return IndexEntry(
        video_id=video_id, title="Midterm Blood Bath, $4T AI Sell Off", published_at="2026-11-07",
        url=f"https://www.youtube.com/watch?v={video_id}", duration_seconds=5583, status=status,
        discovered_at="2026-11-07T00:00:00Z",
        chapters=[Chapter(start_time=0, end_time=217, title="Bestie intros!")],
    )


def seed(store, *entries: IndexEntry) -> None:
    idx = store.load_index()
    for e in entries:
        idx.episodes[e.video_id] = e
        store.save_normalized_transcript(fixture_transcript(e.video_id))
    store.save_index(idx)


def take(quote: str, ts: int, speaker: str = "Jason Calacanis") -> dict:
    return {"speaker": speaker, "attribution_basis": "segment_role", "attribution_evidence": "Welcome back to the number one podcast in the world", 
            "attribution_confidence": "high", "stance_type": "own_view",
            "summary": "s", "quote": quote, "timestamp_seconds": ts}


def pred(claim: str, quote: str | None, ts: int, speaker: str = "David Sacks") -> dict:
    return {"speaker": speaker, "attribution_basis": "indirect", "attribution_evidence": None, 
            "attribution_confidence": "medium", "stance_type": "own_view",
            "claim": claim, "category": "politics", "direction": "up",
            "timeframe": {"text": "2026 count", "date": None, "inferred": True},
            "resolution_criteria": "r", "stated_confidence": None, "confidence_bucket": "unstated",
            "quote": quote, "timestamp_seconds": ts}


def stock(tickers: list[str], ts: int) -> dict:
    return {"claim": "c", "tickers": tickers, "company": None, "is_private": not tickers, "sector": None,
            "direction": "bearish", "timeframe": "12 months", "rationale": "r", "speaker": "Chamath",
            "attribution_basis": "topic_or_style", "attribution_evidence": None, "attribution_confidence": "low",
            "timestamp_seconds": ts,
            "note": None if tickers else "private company"}


def model_output() -> dict:
    return {
        "hosts_present": ["Jason Calacanis", "sacks"],
        "guests": [],
        "key_takes": [take("Over a million views so far", 101)]
        + [take("It doesn't matter how good the economy actually is", 517, "David Sacks")] * 6
        + [take("an invented line that was never spoken anywhere", 30)],
        "predictions": [
            pred("Dems reach 250 House seats", "Dems will have at least 250 seats", 238),
            pred("Inflation keeps rising", "inflation is growing faster than your wages", 543),
            pred("Made-up", "this quote is entirely fabricated by the model", 600, "Guest:  Jane Doe"),
        ],
        "stock_implications": [stock(["nvda"], 211), stock([], 300)],
        "macro_themes": [{"theme": "  AI  Capex ", "stance_summary": "s", "speakers": ["Friedberg"],
                          "timestamp_seconds": 211}],
        "polymarket_candidates": [
            {"prediction_index": 0, "question": "Will Dems win 250+ seats?", "hosts_answer": "yes", "search_keywords": ["test keyword"],
             "implied_probability": 0.8},
            {"prediction_index": 2, "question": "Q3?", "hosts_answer": "no", "search_keywords": ["test keyword"], "implied_probability": None},
            {"prediction_index": 7, "question": "Out of range?", "hosts_answer": "yes", "search_keywords": ["test keyword"], "implied_probability": 0.5},
        ],
    }


def test_run_analyze_end_to_end(tmp_store, settings):
    seed(tmp_store, entry(), entry("otherVid0001", status="analyzed"))
    backend = FakeBackend(responses=[model_output()])
    report = run_analyze(tmp_store, settings, backend)

    assert report.analyzed == [VID] and report.failed == {}
    assert len(backend.calls) == 1  # the already-analyzed episode is out of scope
    assert report.usage.calls == 1 and report.usage.input_tokens > 0

    a = tmp_store.load_analysis(VID)
    assert a is not None
    assert [p.id for p in a.predictions] == [f"{VID}-p01", f"{VID}-p02", f"{VID}-p03"]
    assert [s.id for s in a.stock_implications] == [f"{VID}-s01", f"{VID}-s02"]
    assert [c.prediction_id for c in a.polymarket_candidates] == [f"{VID}-p01", f"{VID}-p03"]
    assert a.episode.title.startswith("Midterm") and a.episode.chapters[0].title == "Bestie intros!"
    assert a.model == settings.model and a.backend == "fake" and a.chunked is False
    assert a.usage == report.usage
    # quote verification applied
    assert a.key_takes[0].quote_verified == "verified"
    assert a.key_takes[7].quote_verified == "dropped" and a.key_takes[7].quote == ""
    assert a.predictions[2].quote_verified == "dropped" and a.predictions[2].quote is None
    assert a.quote_verification.checked == 11
    assert a.quote_verification.dropped == 2
    # attribution cues checked against the transcript; confidence capped at what the evidence supports
    assert a.key_takes[0].attribution_evidence_verified == "verified" and a.key_takes[0].attribution_confidence == "high"
    assert a.key_takes[1].attribution_evidence_verified == "flagged"  # the cited cue is 8+ minutes before the item
    assert a.key_takes[1].attribution_confidence == "medium"
    assert a.predictions[0].attribution_basis == "indirect" and a.predictions[0].attribution_confidence == "medium"
    assert a.attribution_check is not None
    assert (a.attribution_check.items, a.attribution_check.downgraded) == (13, 6)
    assert a.attribution_check.claimed == {"high": 8, "low": 2, "medium": 3}
    assert a.attribution_check.final == {"high": 2, "low": 2, "medium": 9}
    # light normalisation
    assert a.hosts_present == ["Jason Calacanis", "David Sacks"]
    assert a.predictions[2].speaker == "Guest: Jane Doe"
    assert a.stock_implications[0].tickers == ["NVDA"] and a.stock_implications[0].speaker == "Chamath Palihapitiya"
    assert a.macro_themes[0].theme == "ai capex" and a.macro_themes[0].speakers == ["David Friedberg"]

    idx = tmp_store.load_index()
    assert idx.episodes[VID].status == "analyzed" and idx.episodes[VID].analyzed_at == a.analyzed_at


def test_run_analyze_failure_does_not_stop_batch(tmp_store, settings):
    seed(tmp_store, entry("aaaaaaaaaaa"), entry("bbbbbbbbbbb"))

    def handler(call):
        if "aaaaaaaaaaa" in call.label:
            return {"garbage": True}
        return model_output()

    report = run_analyze(tmp_store, settings, FakeBackend(handler=handler),
                         video_ids=["aaaaaaaaaaa", "bbbbbbbbbbb", "missing0000"])
    assert report.analyzed == ["bbbbbbbbbbb"]
    assert set(report.failed) == {"aaaaaaaaaaa", "missing0000"}
    assert report.usage.calls == 1  # only successful episodes are summed
    assert tmp_store.load_index().episodes["aaaaaaaaaaa"].status == "transcribed"


def test_make_ids_and_speakers():
    assert make_ids("v", "p", 3) == ["v-p01", "v-p02", "v-p03"]
    assert make_ids("v", "s", 120)[-1] == "v-s120" and make_ids("v", "s", 120)[0] == "v-s001"
    s = Settings()
    assert normalize_speaker("jason", s) == "Jason Calacanis"
    assert normalize_speaker("guest: x", s) == "Guest: x"
    assert normalize_speaker("unknown", s) == "Unknown"


def test_estimate_math_and_scope(tmp_store, settings):
    seed(tmp_store, entry(), entry("analyzedVid", status="analyzed"))
    report = estimate_episodes(tmp_store, settings)
    assert [e.video_id for e in report.episodes] == [VID]
    (ep,) = report.episodes
    from allin.analyze.pipeline import episode_meta

    tr = tmp_store.load_normalized_transcript(VID)
    system, user = render_analysis_prompt(episode_meta(entry(), tr), tr, settings)
    assert ep.est_input_tokens == estimate_tokens(system + "\n" + user, settings.chars_per_token)
    assert ep.est_output_tokens == settings.est_output_tokens_per_episode
    expected = round(ep.est_input_tokens * PRICE_PER_MTOK["input"] / 1e6
                     + ep.est_output_tokens * PRICE_PER_MTOK["output"] / 1e6, 4)
    assert ep.est_cost_usd_equivalent == expected == report.total_cost_usd_equivalent
    assert ep.words == tr.word_count and ep.chunked is False
    assert report.total_input_tokens == ep.est_input_tokens
    assert "chars/3.6" in report.note and "quota" in report.note

    # an episode that already has an analysis drops out of the default scope
    run_analyze(tmp_store, settings, FakeBackend(responses=[model_output()]))
    assert estimate_episodes(tmp_store, settings).episodes == []
    # explicit ids override the scope; unknown ids are reported in the note
    explicit = estimate_episodes(tmp_store, settings, video_ids=[VID, "nope0000000"])
    assert [e.video_id for e in explicit.episodes] == [VID] and "nope0000000" in explicit.note


def test_estimate_chunked(tmp_store, settings):
    small = Settings(data_dir=settings.data_dir, chunk_threshold_tokens=500, chunk_target_tokens=400)
    seed(tmp_store, entry())
    (ep,) = estimate_episodes(tmp_store, small).episodes
    assert ep.chunked is True
    assert ep.est_output_tokens > small.est_output_tokens_per_episode * 2


def test_estimate_empty_store(tmp_store, settings):
    r = estimate_episodes(tmp_store, settings)
    assert r.episodes == [] and r.total_input_tokens == 0 and r.total_cost_usd_equivalent == 0.0
