"""Resolve predictions from Polymarket outcomes and the hand-edited resolutions.yml."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..models import EpisodeAnalysis, ManualResolution, MarketMatch, PolymarketState, Prediction

_OUTCOME_STATUS = {
    "correct": "resolved_correct",
    "incorrect": "resolved_incorrect",
    "unresolvable": "unresolvable",
}


def _warn(msg: str) -> None:
    print(f"[resolve] {msg}", file=sys.stderr)


def load_manual_resolutions(path: Path | str) -> list[ManualResolution]:
    """Parse resolutions.yml (``{"resolutions": [...]}``).

    A missing file, an empty file, or an empty/null list yields ``[]``. A malformed entry raises
    ``ValueError`` naming the entry (by prediction_id or position) and the offending fields.
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"{p}: top level must be a mapping with a 'resolutions' list")
    items = raw.get("resolutions") or []
    if not isinstance(items, list):
        raise TypeError(f"{p}: 'resolutions' must be a list")
    out: list[ManualResolution] = []
    for i, item in enumerate(items):
        label = item.get("prediction_id", f"#{i}") if isinstance(item, dict) else f"#{i}"
        try:
            if not isinstance(item, dict):
                raise TypeError("entry must be a mapping")
            item = dict(item)
            if item.get("resolved_on") is not None:
                item["resolved_on"] = str(item["resolved_on"])  # yaml parses bare dates
            out.append(ManualResolution.model_validate(item))
        except (ValidationError, TypeError) as exc:
            raise ValueError(f"{p}: bad resolution entry {label!r}: {exc}") from exc
    return out


def _set(pred: Prediction, status: str, source: str, note: str, resolved_at: str, market_id: str | None) -> bool:
    """Apply a resolution; return True when anything observable changed."""
    new = (status, source, note, resolved_at, market_id if market_id else pred.polymarket_market_id)
    old = (pred.status, pred.resolution_source, pred.resolution_note, pred.resolved_at, pred.polymarket_market_id)
    if new == old:
        return False
    pred.status = status  # type: ignore[assignment]
    pred.resolution_source = source
    pred.resolution_note = note
    pred.resolved_at = resolved_at
    pred.polymarket_market_id = new[4]
    return True


def _apply_polymarket(pred: Prediction, match: MarketMatch, today: str) -> bool:
    outcome = (match.resolution_outcome or "").strip()
    if not outcome or pred.status != "pending":
        return False
    low = outcome.lower()
    if low not in ("yes", "no"):
        if pred.resolution_note != f"Polymarket outcome {outcome!r} is not Yes/No; not auto-resolved":
            pred.resolution_note = f"Polymarket outcome {outcome!r} is not Yes/No; not auto-resolved"
            _warn(f"{pred.id}: non Yes/No outcome {outcome!r}; left pending")
        return False
    # Same rule as polymarket.matcher.hosts_were_right: an inverse market asks the negation of the
    # hosts' question, so its "yes" means the hosts' question resolved "no".
    event_happened = (low == "yes") != (match.polarity == "inverse")
    right = event_happened == (match.hosts_answer == "yes")
    status = "resolved_correct" if right else "resolved_incorrect"
    note = f"Polymarket market {match.market_id} resolved {outcome}: {match.market_question}"
    return _set(pred, status, "polymarket", note, today, match.market_id)


def apply_resolutions(
    analyses: list[EpisodeAnalysis],
    polymarket_state: PolymarketState,
    manual: list[ManualResolution],
    today: str,
) -> list[str]:
    """Mutate predictions in place; return ids whose status/resolution changed.

    Polymarket resolves pending predictions only; manual entries always win and are re-applied
    on every run (idempotent). Timeframe expiry never changes status (see ledger flag).
    """
    by_id = {p.id: p for a in analyses for p in a.predictions}
    changed: dict[str, None] = {}

    for m in polymarket_state.matches:
        pred = by_id.get(m.prediction_id)
        if pred is not None and _apply_polymarket(pred, m, today):
            changed[pred.id] = None

    for r in manual:
        pred = by_id.get(r.prediction_id)
        if pred is None:
            _warn(f"warning: resolutions.yml references unknown prediction id {r.prediction_id!r}")
            continue
        note = f"{r.note} ({r.source})"
        status = _OUTCOME_STATUS[r.outcome]
        resolved_at = r.resolved_on
        if resolved_at is None:
            # No date in the yml: keep the date of the first application so re-runs on later days
            # do not rewrite resolved_at (and report a change) every day.
            same = pred.resolution_source == "manual" and pred.status == status and pred.resolution_note == note
            resolved_at = pred.resolved_at if same and pred.resolved_at else today
        if _set(pred, status, "manual", note, resolved_at, None):
            changed[pred.id] = None
    return list(changed)
