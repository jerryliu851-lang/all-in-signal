from __future__ import annotations

import re
from collections import Counter

from allin.ingest.normalize import clean_text, normalize
from allin.models import TranscriptSnippet


def _sn(rows):
    return [TranscriptSnippet(text=t, start=s, duration=d) for t, s, d in rows]


def _words(texts):
    return Counter(w.replace("-", "") for t in texts for w in clean_text(t).split())


def test_entities_and_cues():
    out = normalize("v", _sn([("it&amp;#39;s a [Music] test &gt;&gt; ok", 0, 2), ("[Applause]", 2, 1), ("more", 3, 1)]), "x", True)
    text = out.full_text()
    assert "it's a" in text and ">> ok" in text
    assert "[" not in text
    assert out.word_count == len(text.split())


def test_hyphen_join():
    assert clean_text("inter-\nnational") == "international"


def test_fixture_properties(load_fixture):
    raw = load_fixture("youtube/transcript_2v8b062QzTI_head300.json")
    snippets = _sn([(r["text"], r["start"], r["duration"]) for r in raw])
    a = normalize("v", snippets, "s", True)
    b = normalize("v", snippets, "s", True)
    assert a == b
    assert a.paragraphs and all(p.text for p in a.paragraphs)
    starts = [p.start for p in a.paragraphs]
    assert starts == sorted(starts)
    assert Counter(w.replace("-", "") for w in a.full_text().split()) == _words(r["text"] for r in raw)
    assert a.word_count == len(a.full_text().split())
    for p in a.paragraphs:
        assert len(p.text.split()) <= 120
        assert p.end - p.start <= 45 + 1e-6 or len(p.text.split()) < 15


def test_caps_forced():
    sn = _sn([("word " * 10, i * 5.0, 5.0) for i in range(60)])
    out = normalize("v", sn, "s", True, max_paragraph_seconds=30, max_paragraph_words=40)
    assert len(out.paragraphs) > 5
    for p in out.paragraphs:
        assert len(p.text.split()) <= 40 and p.end - p.start <= 30
    assert out.word_count == 600


def test_sentence_break_after_soft_limit():
    sn = _sn([("one two three four five six seven eight nine ten", i * 3.0, 3.0) for i in range(7)] + [("end here.", 21.0, 2.0), ("next para", 23.0, 2.0)])
    out = normalize("v", sn, "s", True)
    assert len(out.paragraphs) == 2
    assert re.search(r"here\.$", out.paragraphs[0].text)
    assert out.paragraphs[1].start == 23.0
