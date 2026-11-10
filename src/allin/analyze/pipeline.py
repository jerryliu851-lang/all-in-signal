"""``allin analyze``: run the model over transcribed episodes and persist ``EpisodeAnalysis``.

Per episode: load transcript + index entry -> EpisodeMeta -> analyze -> verify quotes -> check
attribution cues -> assign stable ids -> save analysis -> mark the index entry "analyzed". A failure on
one episode is recorded in ``AnalyzeReport.failed`` and does not stop the batch.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..llm import LLMBackend
from ..models import (
    AnalysisOutput,
    AnalyzeReport,
    AttributionCheckSummary,
    EpisodeAnalysis,
    EpisodeMeta,
    IndexEntry,
    KeyTake,
    LLMUsage,
    MacroTheme,
    NormalizedTranscript,
    Prediction,
    QuoteVerificationStatus,
    QuoteVerificationSummary,
    StockImplication,
    StoredCandidate,
)
from ..schema import load_schema
from ..store import Store
from ..util import now_iso
from .analyzer import analyze_episode, log
from .attribution import check_attributions
from .quotes import verify_quotes


def id_width(count: int) -> int:
    """Zero-pad width: 2 digits, more when there are 100+ items."""
    return max(2, len(str(count)))


def make_ids(video_id: str, prefix: str, count: int) -> list[str]:
    """``<video_id>-<prefix>01`` ... in order of appearance."""
    w = id_width(count)
    return [f"{video_id}-{prefix}{i + 1:0{w}d}" for i in range(count)]


def episode_meta(entry: IndexEntry, transcript: NormalizedTranscript | None = None) -> EpisodeMeta:
    """Build EpisodeMeta from an index entry (duration falls back to the transcript's last paragraph)."""
    duration = entry.duration_seconds or 0
    if not duration and transcript is not None and transcript.paragraphs:
        duration = int(transcript.paragraphs[-1].end)
    return EpisodeMeta(
        video_id=entry.video_id,
        title=entry.title,
        published_at=entry.published_at or "",
        url=entry.url,
        duration_seconds=duration,
        chapters=list(entry.chapters),
    )


def normalize_speaker(name: str, settings: Settings) -> str:
    """Map short host names / casing variants to the canonical full name; tidy the Guest prefix."""
    s = " ".join(name.split())
    low = s.lower()
    for full in settings.hosts:
        if low == full.lower() or low == settings.host_short.get(full, "").lower():
            return full
    if low.startswith("guest:"):
        rest = s.split(":", 1)[1].strip()
        return f"Guest: {rest}" if rest else "Unknown"
    if low in ("", "unknown", "unclear"):
        return "Unknown"
    return s


def build_analysis(
    meta: EpisodeMeta,
    output: AnalysisOutput,
    statuses: dict[tuple[str, int], QuoteVerificationStatus],
    summary: QuoteVerificationSummary,
    *,
    settings: Settings,
    backend_name: str,
    chunked: bool,
    usage: LLMUsage,
    cue_statuses: dict[tuple[str, int], QuoteVerificationStatus] | None = None,
    attribution_check: AttributionCheckSummary | None = None,
) -> tuple[EpisodeAnalysis, list[str]]:
    """Assemble the stored EpisodeAnalysis. Returns it with a list of notes (e.g. dropped candidates)."""
    vid = meta.video_id
    notes: list[str] = []
    cues = cue_statuses or {}

    def sp(name: str) -> str:
        return normalize_speaker(name, settings)

    key_takes = [
        KeyTake(
            **{**t.model_dump(), "speaker": sp(t.speaker)},
            quote_verified=statuses.get(("key_takes", i), "none"),
            attribution_evidence_verified=cues.get(("key_takes", i), "none"),
        )
        for i, t in enumerate(output.key_takes)
    ]
    pred_ids = make_ids(vid, "p", len(output.predictions))
    predictions = [
        Prediction(
            **{**p.model_dump(), "speaker": sp(p.speaker)},
            id=pred_ids[i],
            episode_id=vid,
            quote_verified=statuses.get(("predictions", i), "none"),
            attribution_evidence_verified=cues.get(("predictions", i), "none"),
        )
        for i, p in enumerate(output.predictions)
    ]
    stock_ids = make_ids(vid, "s", len(output.stock_implications))
    stocks = [
        StockImplication(
            **{**s.model_dump(), "speaker": sp(s.speaker), "tickers": [t.strip().upper() for t in s.tickers if t.strip()]},
            id=stock_ids[i],
            attribution_evidence_verified=cues.get(("stock_implications", i), "none"),
        )
        for i, s in enumerate(output.stock_implications)
    ]
    themes = [
        MacroTheme(**{**m.model_dump(), "theme": " ".join(m.theme.lower().split()), "speakers": [sp(x) for x in m.speakers]})
        for m in output.macro_themes
    ]
    candidates: list[StoredCandidate] = []
    for c in output.polymarket_candidates:
        if not 0 <= c.prediction_index < len(predictions):
            notes.append(
                f"dropped polymarket candidate with out-of-range prediction_index={c.prediction_index} "
                f"({len(predictions)} predictions): {c.question[:120]}"
            )
            continue
        candidates.append(
            StoredCandidate(
                prediction_id=pred_ids[c.prediction_index],
                question=c.question,
                hosts_answer=c.hosts_answer,
                implied_probability=_valid_prob(c.implied_probability),
                search_keywords=[k.strip() for k in c.search_keywords if k and k.strip()][:4],
            )
        )
    analysis = EpisodeAnalysis(
        episode=meta,
        hosts_present=[sp(h) for h in output.hosts_present],
        guests=list(output.guests),
        key_takes=key_takes,
        predictions=predictions,
        stock_implications=stocks,
        macro_themes=themes,
        polymarket_candidates=candidates,
        analyzed_at=now_iso(),
        model=settings.model,
        backend=backend_name,
        chunked=chunked,
        usage=usage,
        quote_verification=summary,
        attribution_check=attribution_check,
    )
    return analysis, notes


def _valid_prob(p: float | None) -> float | None:
    """Implied probabilities must be in [0, 1]; anything else (e.g. 80 instead of 0.8) becomes None."""
    if p is None:
        return None
    return p if 0.0 <= p <= 1.0 else None


def _scope(store: Store, video_ids: list[str] | None, force: bool = False) -> list[str]:
    if video_ids:
        ids = list(dict.fromkeys(video_ids))
        if force:
            return ids
        keep: list[str] = []
        for vid in ids:
            if store.load_analysis(vid) is not None:
                log(f"{vid}: already analyzed; skipping (use --force to re-run; prediction ids may shift)")
            else:
                keep.append(vid)
        return keep
    return [vid for vid, e in store.load_index().episodes.items() if e.status == "transcribed"]


def analyze_one(
    store: Store, settings: Settings, backend: LLMBackend, video_id: str, schema: dict[str, Any]
) -> EpisodeAnalysis:
    """Analyze and persist one episode; raises on any failure."""
    entry = store.load_index().episodes.get(video_id)
    if entry is None:
        raise LookupError(f"{video_id} is not in the episode index")
    transcript = store.load_normalized_transcript(video_id)
    if transcript is None or not transcript.paragraphs:
        raise LookupError(f"{video_id} has no normalized transcript")
    meta = episode_meta(entry, transcript)
    output, usage, chunked = analyze_episode(meta, transcript, backend, settings, schema)
    statuses, summary = verify_quotes(output, transcript)
    log(
        f"{video_id}: quotes checked={summary.checked} verified={summary.verified} "
        f"flagged={summary.flagged} dropped={summary.dropped}"
    )
    # After quote verification, so cues are measured against the snapped (corrected) timestamps.
    cue_statuses, attribution = check_attributions(output, transcript, settings)
    log(
        f"{video_id}: attribution cues cited={attribution.cited}/{attribution.items} verified={attribution.verified} "
        f"out_of_window={attribution.out_of_window} dropped={attribution.dropped} downgraded={attribution.downgraded} "
        f"claimed={attribution.claimed} final={attribution.final}"
    )
    analysis, notes = build_analysis(
        meta, output, statuses, summary,
        settings=settings, backend_name=getattr(backend, "name", "unknown"), chunked=chunked, usage=usage,
        cue_statuses=cue_statuses, attribution_check=attribution,
    )
    for n in notes:
        log(f"{video_id}: {n}")
    store.save_analysis(analysis)
    index = store.load_index()
    current = index.episodes.get(video_id, entry)
    index.episodes[video_id] = current.model_copy(update={"status": "analyzed", "analyzed_at": analysis.analyzed_at})
    index.updated_at = now_iso()
    store.save_index(index)
    log(
        f"{video_id}: saved {len(analysis.key_takes)} takes, {len(analysis.predictions)} predictions, "
        f"{len(analysis.stock_implications)} stock implications, {len(analysis.polymarket_candidates)} candidates"
    )
    return analysis


def run_analyze(
    store: Store, settings: Settings, backend: LLMBackend, *, video_ids: list[str] | None = None, force: bool = False
) -> AnalyzeReport:
    """Analyze ``video_ids`` (or every index entry with status "transcribed").

    Episodes that already have an analysis are skipped unless ``force`` is set, because prediction
    ids follow the model's output order and a re-run can move ids out from under Polymarket matches
    and ``resolutions.yml`` entries.
    """
    report = AnalyzeReport()
    scope = _scope(store, video_ids, force)
    if not scope:
        log("nothing to analyze")
        return report
    schema = load_schema(settings.schema_path)
    log(f"{len(scope)} episode(s) to analyze with {getattr(backend, 'name', '?')} / {settings.model}")
    for n, vid in enumerate(scope, 1):
        log(f"[{n}/{len(scope)}] {vid}")
        try:
            analysis = analyze_one(store, settings, backend, vid, schema)
        except Exception as e:  # noqa: BLE001 - one bad episode must not stop the batch
            msg = f"{type(e).__name__}: {e}"
            log(f"{vid}: FAILED {msg[:500]}")
            report.failed[vid] = msg
            continue
        report.analyzed.append(vid)
        report.usage = report.usage.add(analysis.usage)
    return report
