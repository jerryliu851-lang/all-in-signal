"""Consumer-facing predictions ledger."""

from __future__ import annotations

from ..models import EpisodeAnalysis, LedgerEntry, PolymarketState, PredictionsLedger
from ..util import now_iso, youtube_url


def build_ledger(analyses: list[EpisodeAnalysis], polymarket_state: PolymarketState, today: str) -> PredictionsLedger:
    """Flatten every prediction (all stance types) into ledger entries, newest episode first."""
    matches = {m.prediction_id: m for m in polymarket_state.matches}
    entries: list[LedgerEntry] = []
    for a in analyses:
        for p in a.predictions:
            m = matches.get(p.id)
            d = p.timeframe.date
            entries.append(
                LedgerEntry(
                    **p.model_dump(),
                    episode_title=a.episode.title,
                    episode_url=a.episode.url,
                    episode_published_at=a.episode.published_at,
                    youtube_timestamp_url=youtube_url(a.episode.video_id, p.timestamp_seconds),
                    polymarket_question=m.market_question if m else None,
                    polymarket_yes_probability=m.market_yes_probability if m else None,
                    polymarket_divergence_points=m.divergence_points if m else None,
                    timeframe_passed=p.status == "pending" and bool(d) and d < today,
                )
            )
    entries.sort(key=lambda e: e.id)
    entries.sort(key=lambda e: e.episode_published_at, reverse=True)
    return PredictionsLedger(updated_at=now_iso(), count=len(entries), predictions=entries)
