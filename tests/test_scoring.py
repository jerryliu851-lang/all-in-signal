"""Offline tests for the scoring unit."""

from __future__ import annotations

from pathlib import Path

import pytest

from allin.config import HOSTS
from allin.models import (
    EpisodeAnalysis,
    EpisodeMeta,
    ManualResolution,
    MarketMatch,
    PolymarketState,
    Prediction,
)
from allin.scoring.ledger import build_ledger
from allin.scoring.pipeline import run_resolve
from allin.scoring.price_scoring import score_stock_calls_by_price
from allin.scoring.resolve import apply_resolutions, load_manual_resolutions
from allin.scoring.scorecard import build_scorecard

FIX = Path(__file__).parent / "fixtures" / "scoring"
TODAY = "2026-11-08"


def pred(n, vid="vid1", speaker="David Sacks", stance="own_view", status="pending", cat="markets",
         bucket="high", date="2027-01-01", ts=60):
    return Prediction(
        id=f"{vid}-p{n:02d}", episode_id=vid, status=status, speaker=speaker,
        attribution_confidence="high", stance_type=stance, claim="c", category=cat, direction="up",
        timeframe={"text": "t", "date": date, "inferred": False}, resolution_criteria="rc",
        stated_confidence=None, confidence_bucket=bucket, quote=None, timestamp_seconds=ts,
    )


def analysis(preds, vid="vid1", published="2026-11-01"):
    return EpisodeAnalysis(
        episode=EpisodeMeta(video_id=vid, title=f"T {vid}", published_at=published,
                            url=f"https://y/{vid}", duration_seconds=3600),
        hosts_present=[], guests=[], key_takes=[], predictions=preds, stock_implications=[],
        macro_themes=[], polymarket_candidates=[], analyzed_at="x", model="m", backend="fake",
    )


def match(pid, answer="yes", outcome=None, **kw):
    return MarketMatch(
        prediction_id=pid, episode_id="vid1", candidate_question="q", market_id="m1",
        market_question="Will it?", closed=True, active=False, hosts_answer=answer,
        resolution_outcome=outcome, match_confidence="high", match_rationale="r",
        matched_at="x", last_checked_at="x", **kw,
    )


def manual(pid, outcome="correct"):
    return ManualResolution(prediction_id=pid, outcome=outcome, note="n", source="http://s")


# ---- yml ------------------------------------------------------------------------------


def test_load_sample_and_missing(tmp_path):
    res = load_manual_resolutions(FIX / "resolutions_sample.yml")
    assert [r.prediction_id for r in res] == ["vid1-p01", "ghost-p99"]
    assert res[0].resolved_on == "2026-12-16"
    assert load_manual_resolutions(tmp_path / "nope.yml") == []
    (tmp_path / "e.yml").write_text("resolutions: []\n")
    assert load_manual_resolutions(tmp_path / "e.yml") == []


def test_bad_entry_names_entry(tmp_path):
    p = tmp_path / "r.yml"
    p.write_text("resolutions:\n  - prediction_id: abc-p01\n    outcome: maybe\n    note: x\n    source: y\n")
    with pytest.raises(ValueError, match="abc-p01"):
        load_manual_resolutions(p)


# ---- resolve --------------------------------------------------------------------------


def test_polymarket_correct_incorrect_and_nonbinary():
    a = analysis([pred(1), pred(2), pred(3)])
    state = PolymarketState(matches=[
        match("vid1-p01", "yes", "Yes"), match("vid1-p02", "yes", "No"), match("vid1-p03", "yes", "Trump"),
    ])
    changed = apply_resolutions([a], state, [], TODAY)
    assert changed == ["vid1-p01", "vid1-p02"]
    p1, p2, p3 = a.predictions
    assert (p1.status, p1.resolution_source, p1.resolved_at, p1.polymarket_market_id) == (
        "resolved_correct", "polymarket", TODAY, "m1")
    assert p2.status == "resolved_incorrect"
    assert p3.status == "pending" and "not Yes/No" in p3.resolution_note
    assert apply_resolutions([a], state, [], TODAY) == []  # idempotent


def test_manual_overrides_polymarket_and_previous():
    a = analysis([pred(1), pred(2, status="resolved_correct")])
    state = PolymarketState(matches=[match("vid1-p01", "yes", "Yes")])
    changed = apply_resolutions([a], state, [manual("vid1-p01", "incorrect"), manual("vid1-p02", "unresolvable")], TODAY)
    assert set(changed) == {"vid1-p01", "vid1-p02"}
    p1, p2 = a.predictions
    assert p1.status == "resolved_incorrect" and p1.resolution_source == "manual"
    assert p1.resolution_note == "n (http://s)" and p1.resolved_at == TODAY
    assert p2.status == "unresolvable"
    assert apply_resolutions([a], state, [manual("vid1-p01", "incorrect"), manual("vid1-p02", "unresolvable")], TODAY) == []


def test_unknown_id_warns(capsys):
    assert apply_resolutions([analysis([pred(1)])], PolymarketState(), [manual("zzz-p01")], TODAY) == []
    assert "zzz-p01" in capsys.readouterr().err


def test_timeframe_passed_flag_and_no_status_change():
    a = analysis([pred(1, date="2026-01-01"), pred(2, date="2027-01-01"), pred(3, date=None),
                  pred(4, date="2026-01-01", status="resolved_correct")])
    apply_resolutions([a], PolymarketState(), [], TODAY)
    assert all(p.status == "pending" for p in a.predictions[:3])
    flags = [e.timeframe_passed for e in build_ledger([a], PolymarketState(), TODAY).predictions]
    assert flags == [True, False, False, False]


# ---- scorecard ------------------------------------------------------------------------


def test_scorecard_counts_and_maps():
    s = "David Sacks"
    a = analysis([
        pred(1, status="resolved_correct", cat="AI", bucket="high"),
        pred(2, status="resolved_correct", cat="AI", bucket="low"),
        pred(3, status="resolved_incorrect", cat="macro", bucket="high"),
        pred(4, status="pending"),
        pred(5, status="unresolvable"),
        pred(6, stance="reported_view", status="resolved_correct"),
        pred(7, stance="devils_advocate", status="resolved_correct"),
        pred(8, speaker="Guest: Bob", status="resolved_incorrect"),
        pred(9, speaker="Unknown"),
    ])
    sc = build_scorecard([a], HOSTS, TODAY)
    assert list(sc.hosts) == list(HOSTS)
    o = sc.hosts[s].overall
    assert (o.total, o.resolved, o.correct, o.incorrect, o.pending, o.unresolvable) == (5, 3, 2, 1, 1, 1)
    assert o.accuracy == 0.667
    assert sc.hosts[s].by_category["AI"].accuracy == 1.0
    assert sc.hosts[s].calibration["high"].resolved == 2
    assert sc.hosts[s].calibration["high"].accuracy == 0.5
    assert sc.hosts["Jason Calacanis"].overall.total == 0
    assert sc.hosts["Jason Calacanis"].overall.accuracy is None
    assert set(sc.others) == {"Guest: Bob", "Unknown"}
    assert sc.others["Guest: Bob"].overall.incorrect == 1


def test_scorecard_none_when_nothing_resolved():
    sc = build_scorecard([analysis([pred(1), pred(2, status="unresolvable")])], HOSTS, TODAY)
    assert sc.hosts["David Sacks"].overall.accuracy is None


# ---- ledger ---------------------------------------------------------------------------


def test_ledger_fields_enrichment_sort():
    a1 = analysis([pred(2), pred(1, stance="reported_view")], vid="vid1", published="2026-10-01")
    a2 = analysis([pred(1, vid="vid2", ts=125)], vid="vid2", published="2026-11-02")
    state = PolymarketState(matches=[match("vid1-p01", market_yes_probability=0.4, divergence_points=30.0)])
    led = build_ledger([a1, a2], state, TODAY)
    assert led.count == 3
    assert [e.id for e in led.predictions] == ["vid2-p01", "vid1-p01", "vid1-p02"]
    e = led.predictions[0]
    assert e.episode_title == "T vid2" and e.episode_url == "https://y/vid2"
    assert e.youtube_timestamp_url == "https://www.youtube.com/watch?v=vid2&t=125s"
    assert e.polymarket_question is None
    e = led.predictions[1]
    assert (e.polymarket_question, e.polymarket_yes_probability, e.polymarket_divergence_points) == ("Will it?", 0.4, 30.0)
    assert e.stance_type == "reported_view"


# ---- stub / pipeline ------------------------------------------------------------------


def test_price_stub():
    with pytest.raises(NotImplementedError, match="Future: price-based scoring"):
        score_stock_calls_by_price([])


def test_run_resolve_empty_store(tmp_store, settings, tmp_path):
    from dataclasses import replace

    s = replace(settings, resolutions_path=tmp_path / "none.yml")
    rep = run_resolve(tmp_store, s)
    assert rep.resolved == [] and rep.still_pending == 0
    assert tmp_store.scorecard_path.exists() and tmp_store.ledger_path.exists()
    assert tmp_store.load_ledger().count == 0
    assert tmp_store.load_scorecard().hosts["David Sacks"].overall.total == 0


def test_run_resolve_persists_changes(tmp_store, settings, tmp_path):
    from dataclasses import replace

    tmp_store.save_analysis(analysis([pred(1), pred(2)]))
    y = tmp_path / "r.yml"
    y.write_text("resolutions:\n  - prediction_id: vid1-p01\n    outcome: correct\n    note: n\n    source: s\n")
    rep = run_resolve(tmp_store, replace(settings, resolutions_path=y))
    assert rep.resolved == ["vid1-p01"] and rep.still_pending == 1
    assert tmp_store.load_analysis("vid1").predictions[0].status == "resolved_correct"


# ---- red-team regressions ------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,outcome,polarity,expected",
    [
        ("yes", "No", "inverse", "resolved_correct"),  # market asks NOT-X and resolved No -> X happened
        ("yes", "Yes", "inverse", "resolved_incorrect"),
        ("no", "Yes", "inverse", "resolved_correct"),
        ("yes", "Yes", "same", "resolved_correct"),
        ("no", "Yes", "same", "resolved_incorrect"),
    ],
)
def test_polymarket_resolution_respects_inverse_polarity(answer, outcome, polarity, expected):
    """Scoring must agree with polymarket.matcher.hosts_were_right (it resolves predictions that a
    re-analysis reset to pending while the match stayed closed+resolved)."""
    from allin.polymarket.matcher import hosts_were_right

    p = pred(1)
    apply_resolutions([analysis([p])], PolymarketState(matches=[match(p.id, answer, outcome, polarity=polarity)]),
                      [], TODAY)
    assert p.status == expected
    assert (p.status == "resolved_correct") == hosts_were_right(answer, outcome, polarity)


def test_manual_without_resolved_on_is_idempotent_across_days():
    p = pred(1)
    a = [analysis([p])]
    assert apply_resolutions(a, PolymarketState(), [manual(p.id)], "2026-11-08") == [p.id]
    assert p.resolved_at == "2026-11-08"
    assert apply_resolutions(a, PolymarketState(), [manual(p.id)], "2026-11-09") == []
    assert p.resolved_at == "2026-11-08"
    # changing the outcome is still applied
    assert apply_resolutions(a, PolymarketState(), [manual(p.id, "incorrect")], "2026-11-10") == [p.id]
    assert p.status == "resolved_incorrect" and p.resolved_at == "2026-11-10"
