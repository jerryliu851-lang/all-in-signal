from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path

import pytest

from allin.ingest import pipeline as P
from allin.ingest import podcast as PC
from allin.ingest import transcripts as T
from allin.ingest import whisper_transcribe as W
from allin.ingest.normalize import normalize
from allin.ingest.podcast import FeedItem
from allin.ingest.youtube import PlaylistEntry
from allin.models import TranscriptSnippet

FEED = Path(__file__).parent / "fixtures" / "podcast" / "allin_feed_head.xml"


@pytest.fixture(scope="module")
def items() -> list[FeedItem]:
    return PC.parse_feed(FEED.read_bytes())


def test_parse_feed(items):
    assert len(items) == 6
    top = items[0]
    assert top.title.startswith("Midterm Blood Bath")
    assert top.pub_date == "2026-11-07" and top.duration_seconds == 5584
    assert top.enclosure_url.startswith("http") and ".mp3" in top.enclosure_url
    assert "(1:24:21) Science Corner" in top.description
    assert items[2].duration_seconds == 55 * 60 + 39


@pytest.mark.parametrize("text,expected", [
    ("01:33:04", 5584), ("55:39", 3339), ("3339", 3339), (" 1:00 ", 60), ("", None), (None, None), ("abc", None),
])
def test_parse_duration(text, expected):
    assert PC.parse_duration(text) == expected


def test_load_feed_cached_and_env(monkeypatch, settings):
    calls = []

    class FakeResp:
        content = FEED.read_bytes()

        def raise_for_status(self):
            pass

    class FakeClient:
        def get(self, url):
            calls.append(url)
            return FakeResp()

    PC._FEED_CACHE.clear()
    monkeypatch.setenv("ALLIN_PODCAST_FEED", "https://example.test/feed.xml")
    a = PC.load_feed(settings, client=FakeClient())
    b = PC.load_feed(settings, client=FakeClient())
    assert a is b and calls == ["https://example.test/feed.xml"]
    PC._FEED_CACHE.clear()


def test_match_by_duration(items):
    m = PC.match_feed_item(items, title="Midterm Blood Bath", duration_seconds=5583)
    assert m is items[0]
    assert PC.match_feed_item(items, title="x", duration_seconds=5147 + 4) is items[1]


def test_match_tie_break_by_title():
    a = FeedItem(title="Alpha beta gamma", duration_seconds=3000)
    b = FeedItem(title="Totally different words", duration_seconds=3002)
    assert PC.match_feed_item([b, a], title="Alpha beta gamma", duration_seconds=3001) is a


def test_match_title_only_and_none(items):
    far = [it.model_copy(update={"duration_seconds": 1}) for it in items]
    m = PC.match_feed_item(far, title=items[1].title, duration_seconds=9999)
    assert m is far[1]
    assert PC.match_feed_item(far, title="Some unrelated cooking show", duration_seconds=9999) is None
    assert PC.match_feed_item(far, title="Some unrelated cooking show", duration_seconds=None) is None


def test_chapters():
    text = "<p>(0:00) Bestie intros!</p> <p>(3:37) Midterms</p><p>(1:05:12) Science Corner</p><p>x</p>"
    ch = PC.parse_chapters_from_description(text)
    assert [(c.start_time, c.title) for c in ch] == [(0.0, "Bestie intros!"), (217.0, "Midterms"), (3912.0, "Science Corner")]
    assert ch[0].end_time == 217.0 and ch[-1].end_time is None
    assert [c.start_time for c in PC.parse_chapters_from_description("12:34 Title\nno")] == [754.0]
    assert PC.parse_chapters_from_description("nothing here https://x.com") == []


def test_meta_from_feed_item(items):
    entry = PlaylistEntry(video_id="vid1", title="YT title", duration_seconds=5583, url="https://yt/vid1")
    meta = PC.meta_from_feed_item(entry, items[0])
    assert (meta.video_id, meta.title, meta.url) == ("vid1", "YT title", "https://yt/vid1")
    assert meta.published_at == "2026-11-07" and meta.duration_seconds == 5584
    assert len(meta.chapters) == 5


def _fake_whisper(monkeypatch, segs, duration=1500.0):
    monkeypatch.setattr(W, "_decode_audio", lambda path: [0.0])

    class Seg:
        def __init__(self, text, start, end):
            self.text, self.start, self.end = text, start, end

    class FakeModel:
        def __init__(self, name, device=None, compute_type=None):
            self.args = (name, device, compute_type)

        def transcribe(self, path, **kw):
            assert kw["language"] == "en" and kw["beam_size"] == 1 and kw["vad_filter"] is True
            return iter([Seg(*s) for s in segs]), types.SimpleNamespace(duration=duration)

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


def test_transcribe_file(monkeypatch, tmp_path):
    _fake_whisper(monkeypatch, [(" Hello there ", 0.0, 2.5), ("   ", 2.5, 3.0), ("Next", 700.0, 705.0)])
    logs = []
    sn, info = W.transcribe_file(tmp_path / "a.mp3", model_name="tiny.en", log=logs.append)
    assert [(s.text, s.start, s.duration) for s in sn] == [("Hello there", 0.0, 2.5), ("Next", 700.0, 5.0)]
    assert info["model"] == "tiny.en" and info["duration"] == 1500.0
    assert info["rtf"] == pytest.approx(info["elapsed_seconds"] / 1500.0)
    assert len(logs) == 1  # one 10-minute progress mark


def test_transcribe_unavailable(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(W.WhisperUnavailable):
        W.transcribe_file(tmp_path / "a.mp3", model_name="tiny.en")


def _boom(*a, **k):
    raise RuntimeError("blocked")


def test_chain_reaches_podcast(monkeypatch, settings, items, tmp_path):
    monkeypatch.setattr(T, "_fetch_via_api", _boom)
    monkeypatch.setattr(T, "_fetch_via_ytdlp", _boom)
    monkeypatch.setattr(PC, "load_feed", lambda s, **k: items)
    seen = {}

    def dl(url, dest, **k):
        seen["url"] = url
        dest.write_bytes(b"abc")
        return dest

    sn = [TranscriptSnippet(text="Hello. World.", start=0.0, duration=2.0)]
    monkeypatch.setattr(PC, "download_enclosure", dl)
    monkeypatch.setattr(W, "transcribe_file", lambda path, **k: (sn, {"model": k["model_name"], "duration": 10.0, "elapsed_seconds": 2.1, "rtf": 0.21}))
    monkeypatch.delenv("ALLIN_WHISPER_MODEL", raising=False)
    res = T.fetch_transcript("v", settings, title="Midterm Blood Bath", duration_seconds=5583)
    assert res.snippets == sn and res.source == "podcast-whisper:small.en" and res.is_generated
    assert res.source_detail and "podcast 5584s vs youtube 5583s" in res.source_detail
    assert "small.en int8 rtf=0.21" in res.source_detail and ".mp3" in res.source_detail
    norm = normalize("v", res.snippets, res.source, res.is_generated, source_detail=res.source_detail)
    assert norm.source_detail == res.source_detail and norm.source == "podcast-whisper:small.en"


def test_chain_all_three_fail(monkeypatch, settings, items):
    monkeypatch.setattr(T, "_fetch_via_api", _boom)
    monkeypatch.setattr(T, "_fetch_via_ytdlp", _boom)
    monkeypatch.setattr(PC, "load_feed", lambda s, **k: items)
    with pytest.raises(T.TranscriptUnavailable) as ei:
        T.fetch_transcript("v", settings, title="Unrelated cooking show", duration_seconds=12)
    r = ei.value.reason
    assert "youtube-transcript-api" in r and "yt-dlp" in r and "podcast-whisper" in r


def test_pipeline_meta_fallback_and_source_detail(monkeypatch, settings, tmp_store, items):
    entry = PlaylistEntry(video_id="vid1", title=items[0].title, duration_seconds=5583, url="u/vid1")
    monkeypatch.setattr(P, "list_playlist_entries", lambda s, limit=None: [entry])
    monkeypatch.setattr(P, "rss_fast_path", lambda s: None)
    monkeypatch.setattr(P, "fetch_video_meta", _boom)
    monkeypatch.setattr(P, "load_feed", lambda s: items)
    sn = [TranscriptSnippet(text="word one two.", start=0.0, duration=2.0)]
    got = {}

    def tr(vid, s, *, title=None, duration_seconds=None):
        got.update(title=title, duration=duration_seconds)
        return T.TranscriptResult(sn, "podcast-whisper:small.en", True, "detail X")

    monkeypatch.setattr(P, "fetch_transcript", tr)
    rep = P.run_ingest(tmp_store, dataclasses.replace(settings))
    assert "meta: podcast feed fallback for vid1" in rep.notes
    ep = tmp_store.load_index().episodes["vid1"]
    assert ep.published_at == "2026-11-07" and len(ep.chapters) == 5 and ep.status == "transcribed"
    assert got == {"title": items[0].title, "duration": 5584}
    assert tmp_store.load_normalized_transcript("vid1").source_detail == "detail X"
