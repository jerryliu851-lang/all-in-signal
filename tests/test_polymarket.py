"""Offline tests for the polymarket work unit."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from allin.llm.fake import FakeBackend
from allin.models import (
    EpisodeAnalysis,
    EpisodeMeta,
    PolymarketState,
    Prediction,
    StoredCandidate,
    Timeframe,
    UnmatchedCandidate,
)
from allin.polymarket.gamma import GammaClient, market_from_api, parse_outcome_prices
from allin.polymarket.matcher import build_queries, compute_divergence, hosts_were_right
from allin.polymarket.pipeline import run_match

FIX = Path(__file__).parent / "fixtures" / "gamma"


def _fx(name: str):
    return json.loads((FIX / name).read_text())


def _client(handler) -> GammaClient:
    return GammaClient(
        base_url="https://gamma.test", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None
    )


def test_parse_outcome_prices():
    assert parse_outcome_prices('["Yes", "No"]', '["0.475", "0.525"]') == {"Yes": 0.475, "No": 0.525}
    assert parse_outcome_prices(None, None) == {}


def test_market_from_api_active_and_closed():
    active = _fx("markets_active.json")[0]
    m = market_from_api(active)
    assert m.market_id == str(active["id"]) and not m.closed and m.resolution_outcome is None
    closed = market_from_api(_fx("market_3244610.json"))
    assert closed.closed and closed.resolution_outcome == "No" and closed.yes_probability == 0.0
    assert closed.uma_resolution_status == "resolved"


def test_search_flattens_events():
    data = _fx("public_search_fed.json")
    expected = sum(len(e["markets"]) for e in data["events"])

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/public-search" and req.url.params["q"] == "fed"
        return httpx.Response(200, json=data)

    out = _client(handler).search("fed", limit_per_type=5)
    assert len(out) == expected
    assert out[0].event_title == data["events"][0]["title"]


def test_retry_on_429_then_success():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429 if calls["n"] == 1 else 503)
        return httpx.Response(200, json=_fx("market_3244610.json"))

    m = _client(handler).get_market("3244610")
    assert m is not None and calls["n"] == 3


def test_get_market_404_is_none():
    assert _client(lambda r: httpx.Response(404)).get_market("1") is None


def _cand(q="Will the Fed cut rates in December 2026?", ans="yes", p=0.8, pid="v1-p01"):
    return StoredCandidate(prediction_id=pid, question=q, hosts_answer=ans, implied_probability=p)


def test_build_queries():
    qs = build_queries(_cand("Will the Fed cut rates at the December 2026 FOMC meeting?"), None)  # type: ignore[arg-type]
    assert qs[0].startswith("Will the Fed")
    assert qs[1] == "Fed cut rates at December 2026 FOMC meeting"


def test_divergence():
    assert compute_divergence(0.8, 0.5, "same") == (30.0, True)
    assert compute_divergence(0.8, 0.7, "same") == (10.0, False)
    d, f = compute_divergence(0.8, 0.9, "inverse")  # market-equivalent yes is 0.1
    assert d == 70.0 and f
    assert compute_divergence(0.5, 0.75, "same")[1] is True  # exactly 25 flags
    assert compute_divergence(None, 0.5, "same") == (None, False)


@pytest.mark.parametrize(
    "ans,res,pol,exp",
    [
        ("yes", "Yes", "same", True),
        ("yes", "No", "same", False),
        ("no", "No", "same", True),
        ("no", "Yes", "same", False),
        ("yes", "No", "inverse", True),
        ("yes", "Yes", "inverse", False),
        ("no", "Yes", "inverse", True),
        ("yes", None, "same", None),
        ("yes", "Yes", "n/a", None),
        ("yes", "Trump", "same", None),
    ],
)
def test_hosts_were_right(ans, res, pol, exp):
    assert hosts_were_right(ans, res, pol) is exp


# ---------------------------------------------------------------- run_match


def _analysis(vid="v1") -> EpisodeAnalysis:
    pred = Prediction(
        id=f"{vid}-p01",
        episode_id=vid,
        speaker="Chamath Palihapitiya",
        attribution_confidence="high",
        stance_type="own_view",
        claim="The Fed will cut in December.",
        category="macro",
        direction="yes",
        timeframe=Timeframe(text="December 2026", date="2026-12-10", inferred=False),
        resolution_criteria="Fed cuts at Dec FOMC.",
        stated_confidence=None,
        confidence_bucket="medium",
        quote=None,
        timestamp_seconds=10,
    )
    return EpisodeAnalysis(
        episode=EpisodeMeta(video_id=vid, title="E1", published_at="2026-11-01", url="u", duration_seconds=3600),
        hosts_present=[],
        guests=[],
        key_takes=[],
        predictions=[pred],
        stock_implications=[],
        macro_themes=[],
        polymarket_candidates=[_cand(pid=f"{vid}-p01")],
        analyzed_at="2026-11-01T00:00:00Z",
        model="m",
        backend="fake",
    )


def _market_obj(mid, closed=False, prices='["0.4", "0.6"]'):
    return {
        "id": mid,
        "question": f"Fed cut? {mid}",
        "slug": f"s{mid}",
        "closed": closed,
        "active": not closed,
        "endDate": "2026-12-10T00:00:00Z",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": prices,
        "umaResolutionStatus": "resolved" if closed else None,
    }


def _gamma(ids, state=None):
    state = state if state is not None else {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/public-search":
            return httpx.Response(
                200, json={"events": [{"title": "Fed", "markets": [_market_obj(i) for i in ids]}] if ids else []}
            )
        mid = req.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json=state.get(mid, _market_obj(mid)))

    return _client(handler)


def _judge(mid="101", match=True, pol="same"):
    return {"match": match, "market_id": mid if match else None, "confidence": "high", "polarity": pol if match else "n/a", "rationale": "same"}


def test_new_candidate_matched(tmp_store, settings):
    tmp_store.save_analysis(_analysis())
    backend = FakeBackend([_judge("101")])
    rep = run_match(tmp_store, settings, backend, gamma=_gamma(["100", "101"]))
    assert rep.matched == ["v1-p01"] and rep.usage.calls == 1
    assert backend.calls[0].label == "match:v1-p01" and backend.calls[0].effort == settings.match_effort
    st = tmp_store.load_polymarket()
    m = st.matches[0]
    assert m.market_id == "101" and m.outcome_prices == {"Yes": 0.4, "No": 0.6}
    assert m.market_yes_probability == 0.4 and m.divergence_points == pytest.approx(40.0) and m.flagged_divergence
    assert m.match_confidence == "high" and m.matched_at and m.last_checked_at and st.updated_at
    assert tmp_store.load_analysis("v1").predictions[0].polymarket_market_id == "101"


def test_judge_no_match_records_seen(tmp_store, settings):
    tmp_store.save_analysis(_analysis())
    rep = run_match(tmp_store, settings, FakeBackend([_judge(match=False)]), gamma=_gamma(["100", "101"]))
    assert rep.unmatched == ["v1-p01"]
    u = tmp_store.load_polymarket().unmatched[0]
    assert u.attempts == 1 and u.seen_market_ids == ["100", "101"] and u.last_attempt_at


def test_no_markets_no_llm_call(tmp_store, settings):
    tmp_store.save_analysis(_analysis())
    backend = FakeBackend([])
    rep = run_match(tmp_store, settings, backend, gamma=_gamma([]))
    assert rep.unmatched == ["v1-p01"] and backend.calls == []
    assert tmp_store.load_polymarket().unmatched[0].seen_market_ids == []


def _seed_unmatched(store, days_ago, seen):
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    store.save_analysis(_analysis())
    store.save_polymarket(
        PolymarketState(
            unmatched=[
                UnmatchedCandidate(
                    prediction_id="v1-p01", episode_id="v1", question="q", hosts_answer="yes",
                    attempts=1, last_attempt_at=when, seen_market_ids=seen,
                )
            ]
        )
    )


def test_cooldown_skip(tmp_store, settings):
    _seed_unmatched(tmp_store, 1, ["100"])
    backend = FakeBackend([])
    rep = run_match(tmp_store, settings, backend, gamma=_gamma(["100", "999"]))
    assert rep.skipped == ["v1-p01"] and backend.calls == []


def test_retry_only_on_unseen_ids(tmp_store, settings):
    _seed_unmatched(tmp_store, 30, ["100", "101"])
    backend = FakeBackend([])
    rep = run_match(tmp_store, settings, backend, gamma=_gamma(["100", "101"]))
    assert rep.skipped == ["v1-p01"] and backend.calls == []
    backend = FakeBackend([_judge("102")])
    rep = run_match(tmp_store, settings, backend, gamma=_gamma(["100", "102"]))
    assert rep.matched == ["v1-p01"] and len(backend.calls) == 1
    st = tmp_store.load_polymarket()
    assert not st.unmatched and st.matches[0].market_id == "102"


def test_refresh_resolves_prediction(tmp_store, settings):
    tmp_store.save_analysis(_analysis())
    run_match(tmp_store, settings, FakeBackend([_judge("101")]), gamma=_gamma(["101"]))
    closed = {"101": _market_obj("101", closed=True, prices='["1", "0"]')}
    rep = run_match(tmp_store, settings, FakeBackend([]), gamma=_gamma(["101"], closed))
    assert rep.resolved_from_closed == ["v1-p01"]
    p = tmp_store.load_analysis("v1").predictions[0]
    assert p.status == "resolved_correct" and p.resolution_source == "polymarket"
    assert p.resolution_note == "Polymarket 'Fed cut? 101' resolved Yes" and p.resolved_at
    m = tmp_store.load_polymarket().matches[0]
    assert m.closed and m.resolution_outcome == "Yes"
    # already resolved: a further run does nothing
    rep = run_match(tmp_store, settings, FakeBackend([]), gamma=_gamma(["101"], closed))
    assert rep.resolved_from_closed == []


def test_inverse_polarity_resolution(tmp_store, settings):
    tmp_store.save_analysis(_analysis())
    run_match(tmp_store, settings, FakeBackend([_judge("101", pol="inverse")]), gamma=_gamma(["101"]))
    m = tmp_store.load_polymarket().matches[0]
    assert m.polarity == "inverse" and m.match_rationale == "same"
    assert m.market_yes_probability == pytest.approx(0.6)
    closed = {"101": _market_obj("101", closed=True, prices='["1", "0"]')}
    run_match(tmp_store, settings, FakeBackend([]), gamma=_gamma(["101"], closed))
    assert tmp_store.load_analysis("v1").predictions[0].status == "resolved_incorrect"


def test_match_without_polarity_is_not_stored_as_same(tmp_store, settings):
    """Red-team regression: match=true with polarity 'n/a' used to be silently stored as 'same',
    which resolves the prediction the wrong way round when the market is actually inverse."""
    tmp_store.save_analysis(_analysis())
    bad = {"match": True, "market_id": "101", "confidence": "medium", "polarity": "n/a", "rationale": "?"}
    rep = run_match(tmp_store, settings, FakeBackend([bad]), gamma=_gamma(["101"]))
    assert rep.matched == [] and rep.unmatched == ["v1-p01"]
    st = tmp_store.load_polymarket()
    assert st.matches == [] and st.unmatched[0].seen_market_ids == ["101"]


def test_non_json_gamma_body_does_not_abort_the_run(tmp_store, settings):
    """Red-team regression: a 200 with an HTML/garbage body raised JSONDecodeError out of run_match."""
    tmp_store.save_analysis(_analysis())
    gamma = _client(lambda r: httpx.Response(200, text="<html>cloudflare</html>"))
    rep = run_match(tmp_store, settings, FakeBackend([]), gamma=gamma)
    assert rep.skipped == ["v1-p01"] and rep.matched == []
