"""Query building, LLM judging and probability math for matching candidates to Polymarket."""

from __future__ import annotations

import re
import sys
from typing import Any, Literal

from pydantic import BaseModel

from ..config import Settings
from ..llm.base import LLMBackend, LLMResult
from ..models import Prediction, StoredCandidate
from .gamma import GammaClient, MarketSummary

Polarity = Literal["same", "inverse", "n/a"]

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["match", "market_id", "confidence", "polarity", "rationale"],
    "properties": {
        "match": {"type": "boolean"},
        "market_id": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "polarity": {"type": "string", "enum": ["same", "inverse", "n/a"]},
        "rationale": {"type": "string"},
    },
}


class JudgeResult(BaseModel):
    """Validated judge output (plus the usage of the call that produced it)."""

    match: bool
    market_id: str | None
    confidence: Literal["high", "medium", "low"]
    polarity: Polarity
    rationale: str
    llm: LLMResult | None = None

    model_config = {"arbitrary_types_allowed": True}


_STOP_RE = re.compile(r"^(will|the|a|an|does|do|is|are|be|to)$", re.IGNORECASE)


def _short_query(question: str, max_tokens: int = 6) -> str:
    """Heuristic short query: capitalized words, numbers and $ amounts, in order (Gamma search likes these)."""
    cleaned = re.sub(r"[?!,:;\"()]", " ", question)
    toks = cleaned.split()
    picked = [t for i, t in enumerate(toks) if (t[:1].isupper() and i > 0 and not _STOP_RE.match(t)) or re.search(r"\d|\$", t)]
    return " ".join(picked[:max_tokens])


def build_queries(candidate: StoredCandidate, prediction: Prediction | None = None) -> list[str]:
    """Search queries, most useful first: the model's search_keywords, a short entity/number query,
    the de-stopworded question, and finally the full question (Gamma's search handles sentences badly)."""
    question = candidate.question.strip()
    queries: list[str] = []

    def add(q: str) -> None:
        q = " ".join(q.split())
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)

    for k in candidate.search_keywords or []:
        add(k)
    add(_short_query(question))
    cleaned = re.sub(r"[?!.,:;\"()]", " ", question)
    add(" ".join(w for w in cleaned.split() if not _STOP_RE.match(w)))
    add(question)
    return queries


def _overlap_score(text: str, terms: set[str]) -> int:
    toks = {t for t in re.findall(r"[a-z0-9$%.]+", text.lower()) if len(t) > 2 and not _STOP_RE.match(t)}
    return len(toks & terms)


def gather_markets(
    gamma: GammaClient, queries: list[str], top_n: int, rank_terms: list[str] | None = None
) -> list[MarketSummary]:
    """Search every query, dedupe by market id, keep Yes/No markets only, rank by keyword overlap with
    ``rank_terms`` (stable: search order breaks ties), cap at ``top_n``."""
    seen: set[str] = set()
    out: list[MarketSummary] = []
    for q in queries:
        for m in gamma.search(q):
            if m.market_id in seen:
                continue
            if set(m.outcomes) != {"Yes", "No"}:
                continue  # multi-outcome markets (candidate X vs Y) cannot answer a yes/no candidate
            seen.add(m.market_id)
            out.append(m)
    if rank_terms:
        terms = {t for s in rank_terms for t in re.findall(r"[a-z0-9$%.]+", s.lower()) if len(t) > 2 and not _STOP_RE.match(t)}
        out.sort(key=lambda m: -_overlap_score(f"{m.question} {m.event_title or ''}", terms))
    return out[:top_n]


def _fmt_market(i: int, m: MarketSummary) -> str:
    status = "closed" if m.closed else ("active" if m.active else "inactive")
    yes = f"{m.yes_probability:.3f}" if m.yes_probability is not None else "n/a"
    res = m.resolution_outcome or "unresolved"
    return (
        f"{i}. market_id={m.market_id} | question: {m.question} | event: {m.event_title or 'n/a'} | "
        f"ends: {m.end_date or 'n/a'} | {status} | yes price: {yes} | resolution: {res}"
    )


def load_prompt(settings: Settings) -> tuple[str, str]:
    """Return (system, user) templates from prompts/match_market.md."""
    text = (settings.prompts_dir / "match_market.md").read_text(encoding="utf-8")
    m = re.search(r"^## SYSTEM\s*\n(.*?)^## USER\s*\n(.*)$", text, re.DOTALL | re.MULTILINE)
    if not m:
        raise ValueError("match_market.md must contain '## SYSTEM' and '## USER' sections")
    return m.group(1).strip(), m.group(2).strip()


def judge_match(
    backend: LLMBackend,
    settings: Settings,
    candidate: StoredCandidate,
    prediction: Prediction,
    markets: list[MarketSummary],
) -> JudgeResult:
    """Ask the model which (if any) market resolves the same question."""
    system, user_t = load_prompt(settings)
    implied = "unknown" if candidate.implied_probability is None else f"{candidate.implied_probability:.2f}"
    values = {
        "question": candidate.question,
        "hosts_answer": candidate.hosts_answer,
        "implied_probability": implied,
        "claim": prediction.claim,
        "resolution_criteria": prediction.resolution_criteria,
        "timeframe": f"{prediction.timeframe.text} (date: {prediction.timeframe.date or 'unspecified'})",
        "markets": "\n".join(_fmt_market(i, m) for i, m in enumerate(markets, 1)),
    }
    user = user_t
    for k, v in values.items():
        user = user.replace("{{" + k + "}}", v)
    res = backend.complete_json(
        system=system,
        user=user,
        schema=JUDGE_SCHEMA,
        effort=settings.match_effort,
        label=f"match:{prediction.id}",
    )
    parsed = JudgeResult.model_validate({**(res.data or {}), "llm": None})
    parsed.llm = res
    valid_ids = {m.market_id for m in markets}
    if parsed.match and (parsed.market_id is None or parsed.market_id not in valid_ids):
        print(f"[match] {prediction.id}: judge returned unknown market id; treating as no match", file=sys.stderr)
        parsed.match = False
        parsed.market_id = None
        parsed.polarity = "n/a"
    elif parsed.match and parsed.polarity not in ("same", "inverse"):
        # A match without a polarity cannot be resolved safely (it used to be stored as "same").
        print(f"[match] {prediction.id}: judge matched without a polarity; treating as no match", file=sys.stderr)
        parsed.match = False
        parsed.market_id = None
        parsed.polarity = "n/a"
    return parsed


def compute_divergence(
    implied_probability: float | None,
    market_yes_probability: float | None,
    polarity: str,
    flag_points: float = 25.0,
) -> tuple[float | None, bool]:
    """Return (divergence in points, flagged). The market yes is flipped for inverse polarity."""
    if implied_probability is None or market_yes_probability is None:
        return None, False
    market = 1.0 - market_yes_probability if polarity == "inverse" else market_yes_probability
    points = round(abs(implied_probability * 100 - market * 100), 4)
    return points, points >= flag_points


def hosts_were_right(hosts_answer: str, resolution_outcome: str | None, polarity: str) -> bool | None:
    """Whether the hosts' yes/no matched the resolved market; None if undeterminable."""
    if resolution_outcome is None or polarity not in ("same", "inverse"):
        return None
    label = resolution_outcome.strip().lower()
    if label not in ("yes", "no"):
        return None
    event_happened = (label == "yes") != (polarity == "inverse")
    return event_happened == (hosts_answer == "yes")
