from __future__ import annotations

import pytest

from allin.ingest import transcripts as T
from allin.models import TranscriptSnippet


def test_parse_json3():
    obj = {"events": [
        {"tStartMs": 0, "dDurationMs": 1500, "id": 1},
        {"tStartMs": 500, "dDurationMs": 2000, "segs": [{"utf8": "hello"}, {"utf8": " world"}]},
        {"tStartMs": 2500, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 3000, "dDurationMs": 1000, "segs": [{"utf8": "a\nb"}]},
    ]}
    out = T.parse_json3(obj)
    assert [(s.text, s.start, s.duration) for s in out] == [("hello world", 0.5, 2.0), ("a b", 3.0, 1.0)]


def test_api_success(monkeypatch, settings):
    sn = [TranscriptSnippet(text="x", start=0, duration=1)]
    monkeypatch.setattr(T, "_fetch_via_api", lambda v, s: (sn, True))
    assert T.fetch_transcript("v", settings) == T.TranscriptResult(sn, "youtube-transcript-api", True)


def test_falls_through(monkeypatch, settings):
    sn = [TranscriptSnippet(text="x", start=0, duration=1)]

    def boom(v, s):
        raise RuntimeError("blocked")

    monkeypatch.setattr(T, "_fetch_via_api", boom)
    monkeypatch.setattr(T, "_fetch_via_ytdlp", lambda v, s: (sn, True))
    assert T.fetch_transcript("v", settings)[1] == "yt-dlp"


def test_both_fail(monkeypatch, settings):
    def boom(v, s):
        raise RuntimeError("nope")

    monkeypatch.setattr(T, "_fetch_via_api", boom)
    monkeypatch.setattr(T, "_fetch_via_ytdlp", boom)
    with pytest.raises(T.TranscriptUnavailable) as ei:
        T.fetch_transcript("v", settings)
    assert "youtube-transcript-api" in ei.value.reason and "yt-dlp" in ei.value.reason
