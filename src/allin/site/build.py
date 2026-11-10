"""Assemble the static dashboard: copy ``site/`` and write ``data/*.json`` beside it.

Contract (see ``allin/cli.py``)::

    build_site(store, settings, out_dir, *, sample_data=False) -> Path

The output is a plain directory GitHub Pages can serve under any sub-path. The page fetches
``data/<file>.json`` with relative URLs only.

Store documents copied as-is when present (else an empty but schema-valid document):
``index.json``, ``predictions.json``, ``scorecard.json``, ``polymarket.json``, ``runs.json`` and
``episodes/<video_id>.json``. Derived documents computed here: ``latest.json``, ``stocks.json``,
``themes.json`` and ``manifest.json``.

When the store has analyses but no ledger yet (``allin resolve`` never ran), the ledger is derived
by calling ``allin.scoring.ledger.build_ledger`` so the ledger view is not empty and the rules cannot drift.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models import (
    EpisodeAnalysis,
    EpisodeIndex,
    PolymarketState,
    PredictionsLedger,
    RunRecord,
    Scorecard,
)
from ..util import atomic_write_text, dump_json, now_iso, today_iso, youtube_url

DIRECTION_SCORE: dict[str, int] = {"bullish": 1, "bearish": -1, "neutral": 0}
PRIVATE_BUCKET = "private_or_no_ticker"


@dataclass
class SiteData:
    """Everything the dashboard needs, already loaded. ``analyses`` is newest episode first."""

    analyses: list[EpisodeAnalysis]
    index: EpisodeIndex
    ledger: PredictionsLedger
    scorecard: Scorecard
    polymarket: PolymarketState
    runs: list[RunRecord]
    synthetic: bool = False


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def _sort_newest_first(analyses: list[EpisodeAnalysis]) -> list[EpisodeAnalysis]:
    return sorted(analyses, key=lambda a: (a.episode.published_at, a.episode.video_id), reverse=True)


def derive_ledger(analyses: list[EpisodeAnalysis], polymarket: PolymarketState, today: str) -> PredictionsLedger:
    """Ledger for an analyze-only store: delegates to ``allin.scoring.ledger.build_ledger`` (single source of truth)."""
    from ..scoring.ledger import build_ledger

    return build_ledger(analyses, polymarket, today)


def load_from_store(store: Any) -> SiteData:
    analyses = _sort_newest_first(store.list_analyses())
    polymarket = store.load_polymarket() or PolymarketState()
    ledger = store.load_ledger()
    if ledger is None:
        ledger = derive_ledger(analyses, polymarket, today_iso())
    scorecard = store.load_scorecard() or Scorecard(updated_at=now_iso())
    return SiteData(
        analyses=analyses,
        index=store.load_index() or EpisodeIndex(),
        ledger=ledger,
        scorecard=scorecard,
        polymarket=polymarket,
        runs=list(store.list_runs()),
        synthetic=False,
    )


# --------------------------------------------------------------------------------------
# Derived documents
# --------------------------------------------------------------------------------------


def derive_latest(data: SiteData) -> dict[str, Any]:
    if not data.analyses:
        return {"analysis": None, "ledger": [], "polymarket_matches": [], "polymarket_unmatched": []}
    a = data.analyses[0]
    vid = a.episode.video_id
    return {
        "analysis": a.model_dump(mode="json"),
        "ledger": [e.model_dump(mode="json") for e in data.ledger.predictions if e.episode_id == vid],
        "polymarket_matches": [m.model_dump(mode="json") for m in data.polymarket.matches if m.episode_id == vid],
        "polymarket_unmatched": [
            u.model_dump(mode="json") for u in data.polymarket.unmatched if u.episode_id == vid
        ],
    }


def _chronological(analyses: list[EpisodeAnalysis]) -> list[EpisodeAnalysis]:
    return sorted(analyses, key=lambda a: (a.episode.published_at, a.episode.video_id))


def derive_stocks(analyses: list[EpisodeAnalysis]) -> dict[str, Any]:
    """Group stock implications by ticker with a cumulative stance series (+1/-1/0 per call)."""
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    private: list[dict[str, Any]] = []
    for a in _chronological(analyses):
        ep = a.episode
        for s in sorted(a.stock_implications, key=lambda s: s.timestamp_seconds):
            call = {
                "implication_id": s.id,
                "episode_id": ep.video_id,
                "episode_title": ep.title,
                "published_at": ep.published_at,
                "speaker": s.speaker,
                "attribution_confidence": s.attribution_confidence,
                "attribution_basis": s.attribution_basis,
                "attribution_evidence": s.attribution_evidence,
                "attribution_evidence_verified": s.attribution_evidence_verified,
                "direction": s.direction,
                "timeframe": s.timeframe,
                "rationale": s.rationale,
                "claim": s.claim,
                "company": s.company,
                "sector": s.sector,
                "timestamp_seconds": s.timestamp_seconds,
                "timestamp_url": youtube_url(ep.video_id, s.timestamp_seconds),
            }
            tickers = list(dict.fromkeys(t.strip().upper() for t in s.tickers if t and t.strip()))
            if not tickers:
                private.append({**call, "is_private": s.is_private, "note": s.note})
                continue
            for t in tickers:
                by_ticker.setdefault(t, []).append({**call, "tickers": tickers})

    out: list[dict[str, Any]] = []
    for ticker, calls in by_ticker.items():
        series: list[dict[str, Any]] = []
        cumulative = 0
        for c in calls:
            delta = DIRECTION_SCORE.get(c["direction"], 0)
            cumulative += delta
            if series and series[-1]["episode_id"] == c["episode_id"]:
                series[-1]["delta"] += delta
                series[-1]["cumulative"] = cumulative
                series[-1]["calls"] += 1
            else:
                series.append({"episode_id": c["episode_id"], "published_at": c["published_at"],
                               "delta": delta, "cumulative": cumulative, "calls": 1})
        counts = {d: sum(1 for c in calls if c["direction"] == d) for d in DIRECTION_SCORE}
        out.append({
            "ticker": ticker,
            "companies": sorted({c["company"] for c in calls if c["company"]}),
            "sectors": sorted({c["sector"] for c in calls if c["sector"]}),
            "net_score": cumulative,
            "call_count": len(calls),
            "bullish": counts["bullish"],
            "bearish": counts["bearish"],
            "neutral": counts["neutral"],
            "first_seen": calls[0]["published_at"],
            "last_seen": calls[-1]["published_at"],
            "series": series,
            "calls": calls,
        })
    out.sort(key=lambda d: (-d["call_count"], -abs(d["net_score"]), d["ticker"]))
    return {"tickers": out, PRIVATE_BUCKET: private}


def _theme_key(theme: str) -> str:
    return " ".join(theme.lower().split())


def derive_themes(analyses: list[EpisodeAnalysis]) -> dict[str, Any]:
    """theme -> frequency, first/last seen and per-episode stance summaries in date order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for a in _chronological(analyses):
        ep = a.episode
        for m in a.macro_themes:
            key = _theme_key(m.theme)
            if not key:
                continue
            grouped.setdefault(key, []).append({
                "episode_id": ep.video_id,
                "episode_title": ep.title,
                "published_at": ep.published_at,
                "stance_summary": m.stance_summary,
                "speakers": list(m.speakers),
                "timestamp_url": youtube_url(ep.video_id, m.timestamp_seconds),
            })
    themes = [
        {
            "theme": key,
            "frequency": len(entries),
            "episode_count": len({e["episode_id"] for e in entries}),
            "first_seen": entries[0]["published_at"],
            "last_seen": entries[-1]["published_at"],
            "entries": entries,
        }
        for key, entries in grouped.items()
    ]
    themes.sort(key=lambda t: t["theme"])
    themes.sort(key=lambda t: t["last_seen"], reverse=True)
    themes.sort(key=lambda t: t["frequency"], reverse=True)
    return {"themes": themes}


def _host_key(short: str) -> str:
    return "".join(ch for ch in short.lower() if ch.isalnum())


# --------------------------------------------------------------------------------------
# Output directory handling
# --------------------------------------------------------------------------------------


def _is_safe_out_dir(out_dir: Path, settings: Settings) -> bool:
    """Only ever delete a directory inside the repo or the system temp dir, never a source dir."""
    out = out_dir.resolve()
    allowed_roots = [settings.root.resolve(), Path(tempfile.gettempdir()).resolve()]
    protected = [settings.root.resolve(), settings.site_src_dir.resolve(), settings.data_dir.resolve(),
                 (settings.root / "src").resolve()]
    for p in protected:
        if out == p or p.is_relative_to(out):
            return False
    for p in (settings.site_src_dir.resolve(), settings.data_dir.resolve()):
        if out.is_relative_to(p):
            return False
    return any(out.is_relative_to(r) and out != r for r in allowed_roots)


def _prepare_out_dir(out_dir: Path, settings: Settings) -> None:
    if not _is_safe_out_dir(out_dir, settings):
        raise ValueError(f"refusing to build into {out_dir}: must be inside the repo or a temp dir, "
                         "and not a source or data directory")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    src = settings.site_src_dir
    if not src.is_dir():
        raise FileNotFoundError(f"site source directory not found: {src}")
    shutil.copytree(src, out_dir)


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def build_site(store: Any, settings: Settings, out_dir: Path, *, sample_data: bool = False) -> Path:
    out_dir = Path(out_dir)
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir

    if sample_data:
        from .sample_data import generate_sample

        data = generate_sample(settings)
    else:
        data = load_from_store(store)

    _prepare_out_dir(out_dir, settings)
    data_dir = out_dir / "data"
    written: list[str] = []

    def write(rel: str, obj: Any) -> None:
        atomic_write_text(data_dir / rel, dump_json(obj))
        written.append(f"data/{rel}")

    write("index.json", data.index.model_dump(mode="json"))
    write("predictions.json", data.ledger.model_dump(mode="json"))
    write("scorecard.json", data.scorecard.model_dump(mode="json"))
    write("polymarket.json", data.polymarket.model_dump(mode="json"))
    write("runs.json", [r.model_dump(mode="json") for r in data.runs])
    (data_dir / "episodes").mkdir(parents=True, exist_ok=True)
    for a in data.analyses:
        write(f"episodes/{a.episode.video_id}.json", a.model_dump(mode="json"))
    write("latest.json", derive_latest(data))
    write("stocks.json", derive_stocks(data.analyses))
    write("themes.json", derive_themes(data.analyses))

    manifest = {
        "built_at": now_iso(),
        "episode_count": len(data.analyses),
        "prediction_count": len(data.ledger.predictions),
        "synthetic": data.synthetic,
        "divergence_flag_points": settings.divergence_flag_points,
        "latest_episode_id": data.analyses[0].episode.video_id if data.analyses else None,
        "episodes": [
            {"video_id": a.episode.video_id, "title": a.episode.title, "published_at": a.episode.published_at}
            for a in data.analyses
        ],
        "hosts": [
            {"name": h, "short": settings.host_short.get(h, h), "key": _host_key(settings.host_short.get(h, h))}
            for h in settings.hosts
        ],
        "files": [*written, "data/manifest.json"],
    }
    atomic_write_text(data_dir / "manifest.json", dump_json(manifest))
    return out_dir
