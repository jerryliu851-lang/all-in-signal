"""Resolve stage: apply resolutions, rebuild scorecard and ledger."""

from __future__ import annotations

import sys

from ..config import Settings
from ..models import ResolveReport
from ..util import today_iso
from .ledger import build_ledger
from .resolve import apply_resolutions, load_manual_resolutions
from .scorecard import build_scorecard


def run_resolve(store, settings: Settings) -> ResolveReport:
    """Resolve pending predictions, persist changed analyses, write scorecard + ledger."""
    today = today_iso()
    analyses = store.list_analyses()
    state = store.load_polymarket()
    manual = load_manual_resolutions(settings.resolutions_path)
    changed = apply_resolutions(analyses, state, manual, today)
    changed_set = set(changed)
    for a in analyses:
        if any(p.id in changed_set for p in a.predictions):
            store.save_analysis(a)
    scorecard = build_scorecard(analyses, settings.hosts, today)
    ledger = build_ledger(analyses, state, today)
    store.save_scorecard(scorecard)
    store.save_ledger(ledger)
    pending = sum(1 for a in analyses for p in a.predictions if p.status == "pending")
    print(
        f"[resolve] {len(analyses)} episodes, {ledger.count} predictions, "
        f"{len(changed)} changed, {pending} pending",
        file=sys.stderr,
    )
    return ResolveReport(
        resolved=changed,
        still_pending=pending,
        scorecard_path=str(getattr(store, "scorecard_path", "scorecard.json")),
        ledger_path=str(getattr(store, "ledger_path", "predictions.json")),
    )
