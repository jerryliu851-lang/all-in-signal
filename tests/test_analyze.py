"""Prompt rendering, chunking and the analyzer call loop (offline, FakeBackend)."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from allin.analyze.analyzer import AnalysisError, analyze_episode
from allin.analyze.chunking import chunk_transcript, estimate_transcript_tokens, needs_chunking
from allin.analyze.prompts import (
    fill,
    load_prompt,
    mmss,
    parse_prompt_text,
    render_analysis_prompt,
    render_consolidation_prompt,
    render_transcript,
)
from allin.config import Settings
from allin.llm.fake import FakeBackend
from allin.models import Chapter, EpisodeMeta, NormalizedTranscript, TranscriptParagraph
from allin.schema import analysis_output_schema

FIXTURE = Path(__file__).parent / "fixtures" / "youtube" / "transcript_2v8b062QzTI_head300.json"
SCHEMA = analysis_output_schema()


def fixture_transcript(per_paragraph: int = 10) -> NormalizedTranscript:
    snippets = json.loads(FIXTURE.read_text(encoding="utf-8"))
    paras = []
    for i in range(0, len(snippets), per_paragraph):
        group = snippets[i : i + per_paragraph]
        text = re.sub(r"\s+", " ", re.sub(r"\[[^\[\]]{1,40}\]", " ", " ".join(s["text"] for s in group))).strip()
        paras.append(TranscriptParagraph(start=group[0]["start"], end=group[-1]["start"] + group[-1]["duration"], text=text))
    return NormalizedTranscript(
        video_id="2v8b062QzTI", source="youtube-transcript-api", paragraphs=paras,
        word_count=sum(len(p.text.split()) for p in paras),
    )


def synthetic_transcript(target_tokens: int, settings: Settings) -> NormalizedTranscript:
    """~target_tokens of rendered transcript, 30 s paragraphs of distinct text."""
    paras, t, i = [], 0.0, 0
    sentence = "the fed will cut rates and the market will rally into year end as capex keeps growing "
    while True:
        paras.append(TranscriptParagraph(start=t, end=t + 30, text=f"paragraph {i} " + sentence * 6))
        t += 30
        i += 1
        tr = NormalizedTranscript(video_id="synth", source="test", paragraphs=paras, word_count=0)
        if i % 50 == 0 and estimate_transcript_tokens(tr, settings) >= target_tokens:
            return tr


META = EpisodeMeta(
    video_id="2v8b062QzTI",
    title="Midterm Blood Bath, $4T AI Sell Off",
    published_at="2026-11-07",
    url="https://www.youtube.com/watch?v=2v8b062QzTI",
    duration_seconds=5583,
    chapters=[
        Chapter(start_time=0, end_time=217, title="Bestie intros!"),
        Chapter(start_time=5061, end_time=5583, title="Science Corner: OpenAI solves Exponential Time Hypothesis"),
    ],
)


def valid_output(n_preds: int = 2, quote: str = "Over a million views so far") -> dict:
    take = {
        "speaker": "Jason Calacanis", "attribution_confidence": "high", "stance_type": "own_view",
        "summary": "The livestream was big.", "quote": quote, "timestamp_seconds": 101,
    }
    pred = {
        "speaker": "David Sacks", "attribution_confidence": "medium", "stance_type": "own_view",
        "claim": "Dems will have at least 250 House seats.", "category": "politics", "direction": "up",
        "timeframe": {"text": "when counting finishes", "date": None, "inferred": True},
        "resolution_criteria": "Final House seat count >= 250.", "stated_confidence": "at least",
        "confidence_bucket": "high", "quote": "Dems will have at least 250 seats", "timestamp_seconds": 238,
    }
    return {
        "hosts_present": ["Jason Calacanis", "David Sacks"],
        "guests": [],
        "key_takes": [take] * 8,
        "predictions": [pred] * n_preds,
        "stock_implications": [
            {
                "claim": "AI capex freeze", "tickers": ["NVDA"], "company": "Nvidia", "is_private": False,
                "sector": "semiconductors", "direction": "bearish", "timeframe": "next 12 months",
                "rationale": "r", "speaker": "Unknown", "attribution_confidence": "low",
                "timestamp_seconds": 211, "note": None,
            }
        ],
        "macro_themes": [
            {"theme": "Elections", "stance_summary": "s", "speakers": ["David Sacks"], "timestamp_seconds": 211}
        ],
        "polymarket_candidates": [
            {"prediction_index": 0, "question": "Will Dems win 250+ House seats?", "hosts_answer": "yes",
             "implied_probability": 0.8}
        ],
    }


# ---------------------------------------------------------------- prompts


def test_mmss():
    assert mmss(0) == "00:00"
    assert mmss(65.9) == "01:05"
    assert mmss(5583) == "93:03"


def test_render_analysis_prompt_fills_everything(settings):
    tr = fixture_transcript()
    system, user = render_analysis_prompt(META, tr, settings)
    for text in (system, user):
        assert "{{" not in text and "}}" not in text
    assert "Midterm Blood Bath" in user and "2026-11-07" in user and "93 minutes" in user
    assert "00:00 Bestie intros!" in user
    assert "84:21 Science Corner: OpenAI solves Exponential Time Hypothesis" in user
    assert "- Chamath Palihapitiya" in user
    assert "[00:00] All right, everybody." in user
    lines = [ln for ln in user.splitlines() if re.match(r"^\[\d{2,}:\d{2}\] ", ln)]
    assert len(lines) == len(tr.paragraphs)
    assert "complete episode transcript" in user
    assert "## SYSTEM" not in system and "Editor's note" not in system


def test_render_transcript_one_line_per_paragraph():
    tr = fixture_transcript()
    out = render_transcript(tr).splitlines()
    assert out[1].startswith(f"[{mmss(tr.paragraphs[1].start)}] ")


def test_prompt_file_contains_key_rules(settings):
    tpl = load_prompt(settings.prompts_dir / "analyze_episode.md")
    s = tpl.system
    for phrase in (
        "Chamath Palihapitiya", "David Sacks", "David Friedberg", "Jason Calacanis",
        '"Guest: <full name as introduced>"', '"Unknown"', "attribution_confidence", "Science Corner",
        "8 to 15", "EVERY explicit or strongly implied forward-looking claim", "inferred=true",
        "resolution_criteria", "confidence_bucket", "tickers []", "lowercase", "implied_probability",
        "NO FABRICATED QUOTES", "verbatim", "dropped automatically", "stance_type", "devils_advocate",
        "reported_view", "Jokes, hyperbole, sarcasm", "include it with low confidence",
        "timestamp_seconds", "do not editorialize".lower(),
    ):
        assert phrase in s or phrase in s.lower(), phrase


def test_consolidation_prompt(settings):
    parts = [valid_output(), valid_output(3)]
    system, user = render_consolidation_prompt(parts, META, settings)
    assert "{{" not in system + user
    assert "prediction_index" in system and "8 to 15" in system
    assert json.loads(user.split("<parts>")[1].split("</parts>")[0]) == parts


def test_parse_and_fill_errors():
    with pytest.raises(ValueError):
        parse_prompt_text("## SYSTEM\nonly system")
    tpl = parse_prompt_text("note\n## SYSTEM\nhi {{a}}\n## USER\n{{b}} {{ a }}")
    assert fill(tpl.user, {"a": "{{b}}", "b": "B"}) == "B {{b}}"  # single pass, no re-substitution
    with pytest.raises(KeyError):
        fill("{{missing}}", {})


# ---------------------------------------------------------------- analyzer


def test_single_pass_returns_validated_output(settings):
    backend = FakeBackend(responses=[valid_output()])
    out, usage, chunked = analyze_episode(META, fixture_transcript(), backend, settings, SCHEMA)
    assert chunked is False
    assert len(out.key_takes) == 8 and out.predictions[0].category == "politics"
    assert usage.calls == 1 and usage.input_tokens > 0
    (call,) = backend.calls
    assert call.label == "analyze:2v8b062QzTI" and call.effort == settings.analysis_effort
    assert call.schema is SCHEMA


def test_validation_retry_then_success(settings):
    bad = valid_output()
    bad["key_takes"][0] = {**bad["key_takes"][0], "attribution_confidence": "certain"}
    backend = FakeBackend(responses=[bad, valid_output()])
    _, usage, _ = analyze_episode(META, fixture_transcript(), backend, settings, SCHEMA)
    assert usage.calls == 2 and len(backend.calls) == 2
    assert "did not validate" in backend.calls[1].user and "attribution_confidence" in backend.calls[1].user
    assert "did not validate" not in backend.calls[0].user


def test_validation_fails_twice_raises(settings):
    backend = FakeBackend(responses=[{"nope": 1}, {"nope": 2}])
    with pytest.raises(AnalysisError):
        analyze_episode(META, fixture_transcript(), backend, settings, SCHEMA)
    assert len(backend.calls) == 2


# ---------------------------------------------------------------- chunking


def test_short_transcript_not_chunked(settings):
    tr = fixture_transcript()
    assert not needs_chunking(tr, settings)
    assert len(chunk_transcript(tr, settings)) == 1


def test_chunking_200k_tokens_overlap_and_calls(settings):
    tr = synthetic_transcript(200_000, settings)
    assert needs_chunking(tr, settings)
    chunks = chunk_transcript(tr, settings)
    assert 3 <= len(chunks) <= 6
    for a, b in itertools.pairwise(chunks):
        assert estimate_transcript_tokens(a, settings) <= settings.chunk_target_tokens
        overlap = [p for p in b.paragraphs if p.start < a.paragraphs[-1].end]
        assert overlap, "neighbouring chunks must overlap"
        assert b.paragraphs[0].start >= a.paragraphs[-1].end - settings.chunk_overlap_seconds - 30
        assert b.paragraphs[0].start > a.paragraphs[0].start
    covered = {p.start for c in chunks for p in c.paragraphs}
    assert covered == {p.start for p in tr.paragraphs}  # nothing lost, timestamps kept

    backend = FakeBackend(handler=lambda call: valid_output())
    _, usage, chunked = analyze_episode(META, tr, backend, settings, SCHEMA)
    assert chunked is True
    assert len(backend.calls) == len(chunks) + 1 == usage.calls
    assert "part 1 of" in backend.calls[0].user
    assert "<parts>" in backend.calls[-1].user and "consolidate" in backend.calls[-1].label
    assert all(c.label.startswith("analyze:2v8b062QzTI") for c in backend.calls)
