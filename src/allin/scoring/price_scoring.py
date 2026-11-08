"""Deliberate hook for price-based scoring of stock calls (not implemented)."""

from __future__ import annotations

from collections.abc import Callable

from ..models import EpisodeAnalysis


def score_stock_calls_by_price(
    analyses: list[EpisodeAnalysis],
    price_provider: Callable[[str, str], float] | None = None,
) -> list[dict]:
    """Score ``StockImplication`` items by realized forward return. NOT IMPLEMENTED.

    Intended contract:
      * Inputs: every ``StockImplication`` across ``analyses`` with non-empty ``tickers``; each
        carries ``direction`` (bullish/bearish/neutral), a free-text ``timeframe`` (parsed into a
        horizon in days) and the call date (``episode.published_at``).
      * ``price_provider(ticker, date_iso) -> float`` returns the adjusted close on or before that
        date. It is injected so this module never fetches prices itself.
      * Output: one record per (implication, ticker) with entry/exit price, forward return over
        the stated horizon, and whether the return agreed with ``direction`` (neutral calls are
        scored against a tolerance band). Calls whose horizon has not elapsed stay pending.
    """
    raise NotImplementedError(
        "score_stock_calls_by_price is a deliberate hook, not implemented; "
        "see README 'Future: price-based scoring'."
    )
