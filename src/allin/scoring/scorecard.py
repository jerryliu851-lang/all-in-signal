"""Per-host scorecard. Counts only own_view predictions."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import EpisodeAnalysis, HostScorecard, Prediction, Scorecard, Stats
from ..util import now_iso


def _add(stats: Stats, pred: Prediction) -> None:
    stats.total += 1
    if pred.status == "resolved_correct":
        stats.correct += 1
    elif pred.status == "resolved_incorrect":
        stats.incorrect += 1
    elif pred.status == "unresolvable":
        stats.unresolvable += 1
    else:
        stats.pending += 1


def _finish(stats: Stats) -> None:
    stats.resolved = stats.correct + stats.incorrect
    stats.accuracy = round(stats.correct / stats.resolved, 3) if stats.resolved else None


def _fill(card: HostScorecard, preds: Iterable[Prediction]) -> None:
    for p in preds:
        _add(card.overall, p)
        _add(card.by_category.setdefault(p.category, Stats()), p)
        _add(card.calibration.setdefault(p.confidence_bucket, Stats()), p)
    for s in (card.overall, *card.by_category.values(), *card.calibration.values()):
        _finish(s)


def build_scorecard(analyses: list[EpisodeAnalysis], hosts: tuple[str, ...] | list[str], today: str) -> Scorecard:
    """Build the scorecard (pure). ``today`` drives date logic; ``updated_at`` is a full UTC timestamp."""
    grouped: dict[str, list[Prediction]] = {}
    for a in analyses:
        for p in a.predictions:
            if p.stance_type == "own_view":
                grouped.setdefault(p.speaker, []).append(p)
    host_cards: dict[str, HostScorecard] = {}
    for h in hosts:
        host_cards[h] = HostScorecard(host=h)
        _fill(host_cards[h], grouped.get(h, []))
    others: dict[str, HostScorecard] = {}
    for speaker, preds in grouped.items():
        if speaker in host_cards:
            continue
        others[speaker] = HostScorecard(host=speaker)
        _fill(others[speaker], preds)
    return Scorecard(updated_at=now_iso(), hosts=host_cards, others=others)
