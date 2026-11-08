from __future__ import annotations

import dataclasses

import pytest

from allin.ingest import pipeline as P
from allin.ingest.transcripts import TranscriptUnavailable
from allin.ingest.youtube import PlaylistEntry, VideoMeta
from allin.models import IndexEntry, TranscriptSnippet
from allin.util import now_iso

SN = [TranscriptSnippet(text=f"word{i} here.", start=i * 2.0, duration=2.0) for i in range(10)]


def entry(vid, dur=5400, in_pl=True):
    return PlaylistEntry(video_id=vid, title=f"T {vid}", duration_seconds=dur, url=f"u/{vid}", in_full_episode_playlist=in_pl)


@pytest.fixture
def env(monkeypatch):
    state = {"entries": [], "fail": set(), "meta_calls": []}

    def lst(settings, limit=None):
        return state["entries"][:limit] if limit else state["entries"]

    def meta(vid, settings):
        state["meta_calls"].append(vid)
        return VideoMeta(video_id=vid, title=f"T {vid}", published_at="2026-11-07", duration_seconds=5400, url=f"u/{vid}")

    def tr(vid, settings):
        if vid in state["fail"]:
            raise TranscriptUnavailable("blocked")
        return SN, "youtube-transcript-api", True

    monkeypatch.setattr(P, "list_playlist_entries", lst)
    monkeypatch.setattr(P, "fetch_video_meta", meta)
    monkeypatch.setattr(P, "fetch_transcript", tr)
    monkeypatch.setattr(P, "rss_fast_path", lambda s: None)
    return state


def test_new_episode_transcribed(env, tmp_store, settings):
    env["entries"] = [entry("a")]
    rep = P.run_ingest(tmp_store, settings)
    assert rep.new_transcribed == ["a"] and rep.discovered == ["a"] and rep.unavailable == []
    assert any("rss" in n for n in rep.notes)
    idx = tmp_store.load_index()
    e = idx.episodes["a"]
    assert e.status == "transcribed" and e.transcript_attempts == 1 and e.published_at == "2026-11-07"
    assert tmp_store.load_raw_transcript("a") and tmp_store.load_normalized_transcript("a").word_count == 20
    assert idx.updated_at
    env["meta_calls"].clear()
    rep2 = P.run_ingest(tmp_store, settings)
    assert rep2.new_transcribed == [] and env["meta_calls"] == []


def test_failure_and_retry(env, tmp_store, settings):
    env["entries"] = [entry("a")]
    env["fail"] = {"a"}
    rep = P.run_ingest(tmp_store, settings)
    e = tmp_store.load_index().episodes["a"]
    assert rep.unavailable == ["a"] and e.status == "transcript_unavailable"
    assert e.transcript_attempts == 1 and e.last_error == "blocked" and e.last_attempt_at
    rep = P.run_ingest(tmp_store, settings)
    assert tmp_store.load_index().episodes["a"].transcript_attempts == 2 and rep.retried == ["a"]
    env["fail"] = set()
    rep = P.run_ingest(tmp_store, settings)
    assert rep.new_transcribed == ["a"] and rep.unavailable == []


def test_max_attempts_stops(env, tmp_store, settings):
    s = dataclasses.replace(settings, max_transcript_attempts=2)
    env["entries"] = [entry("a")]
    env["fail"] = {"a"}
    for _ in range(4):
        P.run_ingest(tmp_store, s)
    assert tmp_store.load_index().episodes["a"].transcript_attempts == 2
    idx = tmp_store.load_index()
    idx.episodes["a"].last_attempt_at = "2020-01-01T00:00:00Z"
    tmp_store.save_index(idx)
    P.run_ingest(tmp_store, s)
    assert tmp_store.load_index().episodes["a"].transcript_attempts == 3


def test_excluded_recorded_and_refreshed(env, tmp_store, settings):
    env["entries"] = [entry("short", dur=1500, in_pl=False), entry("a")]
    rep = P.run_ingest(tmp_store, settings)
    assert [x.video_id for x in rep.excluded] == ["short"]
    assert rep.excluded[0].reason.startswith("duration_1500s")
    assert "short" not in env["meta_calls"]
    idx = tmp_store.load_index()
    assert "short" in idx.excluded and "short" not in idx.episodes
    idx.excluded["short"].seen_at = "2020-01-01T00:00:00Z"
    tmp_store.save_index(idx)
    rep = P.run_ingest(tmp_store, settings)
    assert [x.video_id for x in rep.excluded] == ["short"]
    assert tmp_store.load_index().excluded["short"].seen_at != "2020-01-01T00:00:00Z"


def test_non_playlist_excluded_after_meta(env, tmp_store, settings):
    env["entries"] = [entry("x", dur=None, in_pl=False)]
    rep = P.run_ingest(tmp_store, settings)
    assert rep.excluded[0].reason == "not_in_full_episode_playlist"


def test_backfill_limit(env, tmp_store, settings):
    env["entries"] = [entry(f"e{i}") for i in range(6)]
    idx = tmp_store.load_index()
    idx.episodes["old"] = IndexEntry(video_id="old", title="old", url="u", discovered_at=now_iso())
    tmp_store.save_index(idx)
    rep = P.run_ingest(tmp_store, settings, limit=2, backfill=True)
    assert sorted(rep.new_transcribed) == ["e0", "e1"]
    assert tmp_store.load_index().episodes["old"].status == "discovered"
    assert "e2" not in tmp_store.load_index().episodes


def test_one_failure_does_not_stop_others(env, tmp_store, settings, monkeypatch):
    env["entries"] = [entry("a"), entry("b"), entry("c")]
    env["fail"] = {"b"}
    orig = P.fetch_video_meta

    def meta(vid, s):
        if vid == "a":
            raise RuntimeError("meta boom")
        return orig(vid, s)

    monkeypatch.setattr(P, "fetch_video_meta", meta)
    rep = P.run_ingest(tmp_store, settings)
    assert rep.new_transcribed == ["c"] and rep.unavailable == ["b"]
    assert any("a:" in n for n in rep.notes)


def test_rss_adds_candidates(env, tmp_store, settings, monkeypatch):
    env["entries"] = [entry("a")]
    monkeypatch.setattr(P, "rss_fast_path", lambda s: ["a", "zzz"])
    rep = P.run_ingest(tmp_store, settings)
    assert "zzz" in rep.discovered
    assert "zzz" in tmp_store.load_index().excluded


def test_upcoming_premiere_is_reclassified_once_it_airs(env, tmp_store, settings, monkeypatch):
    """Red-team regression: a premiere listed in the playlist before it airs used to be excluded
    with 'live_or_upcoming' forever and never ingested."""
    upcoming = PlaylistEntry(video_id="prem", title="T prem", duration_seconds=None, url="u/prem",
                             in_full_episode_playlist=True, live_status="is_upcoming")
    env["entries"] = [upcoming]

    def meta_upcoming(vid, s):
        return VideoMeta(video_id=vid, title="T prem", duration_seconds=None, live_status="is_upcoming", url="u/prem")

    monkeypatch.setattr(P, "fetch_video_meta", meta_upcoming)
    rep = P.run_ingest(tmp_store, settings)
    assert rep.excluded[0].reason == "live_or_upcoming" and rep.new_transcribed == []
    # it airs: same playlist entry, now with a duration and no live status
    env["entries"] = [entry("prem")]
    monkeypatch.setattr(P, "fetch_video_meta", lambda vid, s: VideoMeta(
        video_id=vid, title="T prem", published_at="2026-11-08", duration_seconds=5400, url="u/prem"))
    rep = P.run_ingest(tmp_store, settings)
    assert rep.new_transcribed == ["prem"]
    idx = tmp_store.load_index()
    assert "prem" not in idx.excluded and idx.episodes["prem"].status == "transcribed"


def test_forward_only_never_reaches_past_known_episodes(env, tmp_store, settings):
    """Cron runs must not discover episodes older than the newest one already tracked (backfill scope)."""
    idx = tmp_store.load_index()
    idx.episodes["known"] = IndexEntry(video_id="known", title="T known", url="u/known", discovered_at=now_iso(),
                                       status="analyzed", duration_seconds=5400)
    tmp_store.save_index(idx)
    env["entries"] = [entry("new"), entry("known"), entry("older1"), entry("older2")]
    rep = P.run_ingest(tmp_store, settings)
    after = tmp_store.load_index()
    assert "new" in after.episodes and after.episodes["new"].status == "transcribed"
    assert "older1" not in after.episodes and "older2" not in after.episodes
    assert "older1" not in after.excluded
    assert any("forward-only" in n for n in rep.notes)
    assert env["meta_calls"] == ["new"]  # no metadata fetch for the older entries either


def test_backfill_mode_still_walks_past_known_episodes(env, tmp_store, settings):
    idx = tmp_store.load_index()
    idx.episodes["known"] = IndexEntry(video_id="known", title="T known", url="u/known", discovered_at=now_iso(),
                                       status="analyzed", duration_seconds=5400)
    tmp_store.save_index(idx)
    env["entries"] = [entry("known"), entry("older1")]
    P.run_ingest(tmp_store, settings, limit=2, backfill=True)
    assert "older1" in tmp_store.load_index().episodes
