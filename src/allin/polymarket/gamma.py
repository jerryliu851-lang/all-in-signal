"""Thin client and parsers for the public Polymarket Gamma API (no auth needed)."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

DEFAULT_BASE_URL = "https://gamma-api.polymarket.com"
RESOLVED_PRICE = 0.99


class MarketSummary(BaseModel):
    """One Polymarket market, normalized."""

    market_id: str
    question: str
    slug: str | None = None
    event_title: str | None = None
    closed: bool = False
    active: bool = True
    end_date: str | None = None
    outcomes: list[str] = Field(default_factory=list)
    outcome_prices: dict[str, float] = Field(default_factory=dict)
    uma_resolution_status: str | None = None
    yes_probability: float | None = None
    resolution_outcome: str | None = None


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_outcome_prices(outcomes_json: Any, prices_json: Any) -> dict[str, float]:
    """Zip the JSON-encoded outcomes and prices strings into ``{label: price}``."""
    outcomes = [str(o) for o in _loads_list(outcomes_json)]
    out: dict[str, float] = {}
    for label, raw in zip(outcomes, _loads_list(prices_json), strict=False):
        try:
            out[label] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def market_from_api(obj: dict[str, Any], event_title: str | None = None) -> MarketSummary:
    """Build a MarketSummary from a Gamma market object."""
    outcomes = [str(o) for o in _loads_list(obj.get("outcomes"))]
    prices = parse_outcome_prices(obj.get("outcomes"), obj.get("outcomePrices"))
    if event_title is None:
        events = obj.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            event_title = events[0].get("title")
    closed = bool(obj.get("closed"))
    uma = obj.get("umaResolutionStatus")
    yes = next((p for label, p in prices.items() if label.strip().lower() == "yes"), None)
    resolution = None
    if closed and prices:
        label, price = max(prices.items(), key=lambda kv: kv[1])
        if price >= RESOLVED_PRICE:
            resolution = label
    return MarketSummary(
        market_id=str(obj.get("id")),
        question=str(obj.get("question") or ""),
        slug=obj.get("slug"),
        event_title=event_title,
        closed=closed,
        active=bool(obj.get("active", True)),
        end_date=obj.get("endDate"),
        outcomes=outcomes,
        outcome_prices=prices,
        uma_resolution_status=uma,
        yes_probability=yes,
        resolution_outcome=resolution,
    )


class GammaClient:
    """Gamma API client with retry/backoff on 429 and 5xx."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
        attempts: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)
        self.attempts = attempts
        self.backoff = backoff
        self._sleep = sleep

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        url = f"{self.base_url}{path}"
        last: httpx.Response | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self.attempts:
                    raise
                print(f"[gamma] {path}: {exc!r}; retry {attempt}", file=sys.stderr)
                self._sleep(self.backoff * 2 ** (attempt - 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = resp
                if attempt < self.attempts:
                    print(f"[gamma] {path}: HTTP {resp.status_code}; retry {attempt}", file=sys.stderr)
                    self._sleep(self.backoff * 2 ** (attempt - 1))
                    continue
                break
            return resp
        assert last is not None
        last.raise_for_status()
        return last

    def search(self, query: str, limit_per_type: int | None = None) -> list[MarketSummary]:
        """Search events and flatten them into markets (carrying the event title)."""
        params: dict[str, Any] = {"q": query, "page": 1}
        if limit_per_type is not None:
            params["limit_per_type"] = limit_per_type
        resp = self._get("/public-search", params)
        resp.raise_for_status()
        out: list[MarketSummary] = []
        for event in resp.json().get("events") or []:
            title = event.get("title")
            for m in event.get("markets") or []:
                out.append(market_from_api(m, event_title=title))
        return out

    def get_market(self, market_id: str) -> MarketSummary | None:
        """Fetch one market by id; None when it does not exist."""
        resp = self._get(f"/markets/{market_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return market_from_api(resp.json())

    def list_markets(self, closed: bool, limit: int = 20, order: str = "volume24hr") -> list[MarketSummary]:
        """List markets ordered descending by ``order``."""
        params = {
            "closed": str(closed).lower(),
            "active": "true",
            "limit": limit,
            "order": order,
            "ascending": "false",
        }
        resp = self._get("/markets", params)
        resp.raise_for_status()
        return [market_from_api(m) for m in resp.json()]
