"""Regression tests for the orchestrator's integration fixes (idle-run idempotence, re-analysis guard, etc.)."""

from __future__ import annotations

import json

from allin.analyze.pipeline import _valid_prob, run_analyze
from allin.config import Settings
from allin.llm.fake import FakeBackend
from allin.models import EpisodeAnalysis, EpisodeIndex, EpisodeMeta, IndexEntry
from allin.polymarket.gamma import market_from_api
from allin.polymarket.matcher import gather_markets


def _analysis(vid: str) -> EpisodeAnalysis:
    return EpisodeAnalysis(
        episode=EpisodeMeta(video_id=vid, title=vid, published_at="2026-11-01", url="u", duration_seconds=5000),
        hosts_present=[], guests=[], key_takes=[], predictions=[], stock_implications=[],
        macro_themes=[], polymarket_candidates=[], analyzed_at="2026-11-01T00:00:00Z", model="m", backend="fake",
    )


def test_store_skips_writes_that_only_change_timestamps(tmp_store):
    idx = EpisodeIndex(updated_at="2026-11-08T00:00:00Z")
    idx.episodes["a"] = IndexEntry(video_id="a", title="t", url="u", discovered_at="2026-11-08T00:00:00Z")
    tmp_store.save_index(idx)
    first = tmp_store.index_path.read_text()
    idx2 = idx.model_copy(update={"updated_at": "2026-11-09T00:00:00Z"})
    tmp_store.save_index(idx2)
    assert tmp_store.index_path.read_text() == first  # untouched: only updated_at differed
    idx2.episodes["b"] = IndexEntry(video_id="b", title="t", url="u", discovered_at="2026-11-09T00:00:00Z")
    tmp_store.save_index(idx2)
    assert json.loads(tmp_store.index_path.read_text())["updated_at"] == "2026-11-09T00:00:00Z"


def test_run_analyze_skips_existing_analysis_unless_forced(tmp_store, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", llm_backend="fake")
    tmp_store.save_analysis(_analysis("x"))
    idx = tmp_store.load_index()
    idx.episodes["x"] = IndexEntry(video_id="x", title="t", url="u", discovered_at="d", status="analyzed")
    tmp_store.save_index(idx)
    backend = FakeBackend(responses=[])
    report = run_analyze(tmp_store, settings, backend, video_ids=["x"])
    assert report.analyzed == [] and report.failed == {} and backend.calls == []
    forced = run_analyze(tmp_store, settings, backend, video_ids=["x"], force=True)
    assert "x" in forced.failed  # attempted (fails here only because the tmp store has no transcript)


def test_gather_markets_keeps_only_yes_no_markets():
    yes_no = market_from_api({"id": "1", "question": "Will it?", "outcomes": '["Yes", "No"]',
                              "outcomePrices": '["0.4", "0.6"]', "closed": False, "active": True})
    multi = market_from_api({"id": "2", "question": "Who wins?", "outcomes": '["Trump", "Harris"]',
                             "outcomePrices": '["0.5", "0.5"]', "closed": False, "active": True})

    class G:
        def search(self, q):
            return [multi, yes_no]

    assert [m.market_id for m in gather_markets(G(), ["q"], 5)] == ["1"]


def test_valid_prob_rejects_out_of_range():
    assert _valid_prob(0.8) == 0.8
    assert _valid_prob(0.0) == 0.0 and _valid_prob(1.0) == 1.0
    assert _valid_prob(80) is None
    assert _valid_prob(-0.1) is None
    assert _valid_prob(None) is None
