from __future__ import annotations

from allin.models import (
    EpisodeAnalysis,
    EpisodeIndex,
    EpisodeMeta,
    IndexEntry,
    NormalizedTranscript,
    RunRecord,
    TranscriptParagraph,
    TranscriptSnippet,
)


def test_index_roundtrip(tmp_store):
    idx = tmp_store.load_index()
    assert idx == EpisodeIndex()
    idx.episodes["abc"] = IndexEntry(video_id="abc", title="t", url="u", discovered_at="2026-11-07T00:00:00Z")
    tmp_store.save_index(idx)
    again = tmp_store.load_index()
    assert again.episodes["abc"].status == "discovered"


def test_transcript_roundtrip(tmp_store):
    snips = [TranscriptSnippet(text="hi", start=0.0, duration=1.0)]
    tmp_store.save_raw_transcript("abc", snips, source="youtube-transcript-api")
    assert tmp_store.load_raw_transcript("abc") == snips
    nt = NormalizedTranscript(video_id="abc", source="youtube-transcript-api",
                              paragraphs=[TranscriptParagraph(start=0, end=1, text="hi")], word_count=1)
    tmp_store.save_normalized_transcript(nt)
    assert tmp_store.load_normalized_transcript("abc") == nt
    assert tmp_store.load_normalized_transcript("missing") is None


def test_analysis_listing_sorted_newest_first(tmp_store):
    def mk(vid, date):
        return EpisodeAnalysis(
            episode=EpisodeMeta(video_id=vid, title=vid, published_at=date, url="u", duration_seconds=5000),
            hosts_present=[], guests=[], key_takes=[], predictions=[], stock_implications=[],
            macro_themes=[], polymarket_candidates=[], analyzed_at="2026-11-07T00:00:00Z",
            model="m", backend="fake",
        )
    tmp_store.save_analysis(mk("old", "2026-10-01"))
    tmp_store.save_analysis(mk("new", "2026-11-01"))
    ids = [a.episode.video_id for a in tmp_store.list_analyses()]
    assert ids == ["new", "old"]


def test_run_log_capped(tmp_store):
    for i in range(5):
        tmp_store.append_run(RunRecord(started_at="a", finished_at="b", command=f"c{i}"), keep_last=3)
    assert [r.command for r in tmp_store.list_runs()] == ["c2", "c3", "c4"]
