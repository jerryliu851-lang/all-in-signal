"""run_match: match stored candidates to Polymarket, refresh prices, resolve closed matches."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import httpx

from ..config import Settings
from ..llm.base import LLMBackend, LLMError
from ..models import (
    EpisodeAnalysis,
    LLMUsage,
    MarketMatch,
    MatchReport,
    PolymarketState,
    StoredCandidate,
    UnmatchedCandidate,
)
from ..store.base import Store
from ..util import now_iso
from .gamma import GammaClient, MarketSummary
from .matcher import build_queries, compute_divergence, gather_markets, hosts_were_right, judge_match


def _log(msg: str) -> None:
    print(f"[match] {msg}", file=sys.stderr)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _market_yes_for_hosts(market: MarketSummary, polarity: str) -> float | None:
    """Probability, in the hosts' question frame, that the question resolves yes."""
    if market.yes_probability is None:
        return None
    return 1.0 - market.yes_probability if polarity == "inverse" else market.yes_probability


def _apply_market(match: MarketMatch, market: MarketSummary, settings: Settings, now: str) -> None:
    pol = match.polarity
    match.market_question = market.question
    match.event_title = market.event_title or match.event_title
    match.slug = market.slug or match.slug
    match.closed = market.closed
    match.active = market.active
    match.end_date = market.end_date or match.end_date
    match.outcome_prices = market.outcome_prices
    match.market_yes_probability = _market_yes_for_hosts(market, pol)
    match.divergence_points, match.flagged_divergence = compute_divergence(
        match.implied_probability, market.yes_probability, pol, settings.divergence_flag_points
    )
    match.resolution_outcome = market.resolution_outcome
    match.last_checked_at = now


def _resolve_prediction(
    analysis: EpisodeAnalysis, match: MarketMatch, now: str
) -> bool:
    """Mark the matching Prediction resolved from a closed market. True if changed."""
    if not (match.closed and match.resolution_outcome):
        return False
    right = hosts_were_right(match.hosts_answer, match.resolution_outcome, match.polarity)
    if right is None:
        return False
    for p in analysis.predictions:
        if p.id == match.prediction_id:
            if p.status != "pending":
                return False
            p.status = "resolved_correct" if right else "resolved_incorrect"
            p.resolution_source = "polymarket"
            p.resolution_note = f"Polymarket '{match.market_question}' resolved {match.resolution_outcome}"
            p.resolved_at = now
            p.polymarket_market_id = match.market_id
            return True
    return False


def run_match(
    store: Store,
    settings: Settings,
    backend: LLMBackend,
    *,
    video_ids: list[str] | None = None,
    gamma: GammaClient | None = None,
) -> MatchReport:
    """Match new candidates, retry stale unmatched ones, refresh and resolve existing matches."""
    gamma = gamma or GammaClient(base_url=settings.gamma_base_url)
    report = MatchReport()
    usage = LLMUsage()
    state: PolymarketState = store.load_polymarket()
    all_analyses = store.list_analyses()
    analyses = all_analyses
    if video_ids:
        wanted = set(video_ids)
        analyses = [a for a in analyses if a.episode.video_id in wanted]
    known_ids = {p.id for a in all_analyses for p in a.predictions}
    by_episode = {a.episode.video_id: a for a in analyses}
    dirty: set[str] = set()
    now = now_iso()
    cooldown = timedelta(days=settings.match_retry_days)

    matches = {m.prediction_id: m for m in state.matches}
    unmatched = {u.prediction_id: u for u in state.unmatched}

    # --- new candidates and unmatched retries -------------------------------------
    for analysis in analyses:
        vid = analysis.episode.video_id
        preds = {p.id: p for p in analysis.predictions}
        for cand in analysis.polymarket_candidates:
            pid = cand.prediction_id
            if pid in matches:
                continue
            prior = unmatched.get(pid)
            prediction = preds.get(pid)
            if prediction is None:
                _log(f"{pid}: no prediction with this id; skipping")
                continue
            if prior is not None:
                last = _parse_time(prior.last_attempt_at)
                if last is not None and _parse_time(now) - last < cooldown:  # type: ignore[operator]
                    report.skipped.append(pid)
                    continue
            try:
                markets = gather_markets(
                    gamma, build_queries(cand, prediction), settings.match_top_n,
                    rank_terms=[cand.question, *cand.search_keywords],
                )
            except (httpx.HTTPError, ValueError) as exc:  # ValueError: non-JSON body from Gamma
                _log(f"{pid}: Gamma search failed ({exc!r}); will retry next run")
                report.skipped.append(pid)
                continue
            if prior is not None and not ({m.market_id for m in markets} - set(prior.seen_market_ids)):
                report.skipped.append(pid)
                continue
            if not markets:
                unmatched[pid] = _unmatched(cand, vid, prior, [], now)
                report.unmatched.append(pid)
                _log(f"{pid}: no markets found")
                continue
            try:
                verdict = judge_match(backend, settings, cand, prediction, markets)
            except (LLMError, ValueError) as exc:
                _log(f"{pid}: judge failed ({exc})")
                report.skipped.append(pid)
                continue
            if verdict.llm is not None:
                usage = usage.add(verdict.llm.usage)
            chosen = next((m for m in markets if m.market_id == verdict.market_id), None)
            if verdict.match and chosen is not None:
                mm = MarketMatch(
                    prediction_id=pid,
                    episode_id=vid,
                    candidate_question=cand.question,
                    market_id=chosen.market_id,
                    market_question=chosen.question,
                    closed=chosen.closed,
                    active=chosen.active,
                    hosts_answer=cand.hosts_answer,
                    implied_probability=cand.implied_probability,
                    match_confidence=verdict.confidence,
                    match_rationale=verdict.rationale,
                    polarity="inverse" if verdict.polarity == "inverse" else "same",
                    matched_at=now,
                    last_checked_at=now,
                )
                _apply_market(mm, chosen, settings, now)
                matches[pid] = mm
                unmatched.pop(pid, None)
                prediction.polymarket_market_id = chosen.market_id
                dirty.add(vid)
                report.matched.append(pid)
                _log(f"{pid}: matched {chosen.market_id} ({verdict.confidence}, {verdict.polarity})")
                if _resolve_prediction(analysis, mm, now):
                    report.resolved_from_closed.append(pid)
            else:
                unmatched[pid] = _unmatched(cand, vid, prior, [m.market_id for m in markets], now, verdict.rationale)
                report.unmatched.append(pid)
                _log(f"{pid}: judge found no match ({verdict.rationale[:160]})")

    # --- refresh existing matches --------------------------------------------------
    for pid, mm in list(matches.items()):
        if mm.episode_id not in by_episode:
            continue
        if pid in report.matched:
            continue
        if mm.closed and mm.resolution_outcome:
            continue
        try:
            fresh = gamma.get_market(mm.market_id)
        except (httpx.HTTPError, ValueError) as exc:
            _log(f"{pid}: refresh failed ({exc!r})")
            continue
        if fresh is None:
            _log(f"{pid}: market {mm.market_id} no longer exists")
            continue
        _apply_market(mm, fresh, settings, now)
        analysis = by_episode[mm.episode_id]
        if _resolve_prediction(analysis, mm, now):
            dirty.add(mm.episode_id)
            report.resolved_from_closed.append(pid)
            _log(f"{pid}: resolved from closed market ({mm.resolution_outcome})")

    for vid in dirty:
        store.save_analysis(by_episode[vid])
    # Prune bookkeeping for prediction ids that no longer exist (an episode was re-analyzed with --force).
    for stale in [pid for pid in matches if pid not in known_ids]:
        _log(f"{stale}: prediction no longer exists; dropping its market match")
        matches.pop(stale)
    for stale in [pid for pid in unmatched if pid not in known_ids]:
        _log(f"{stale}: prediction no longer exists; dropping its unmatched record")
        unmatched.pop(stale)
    state.matches = list(matches.values())
    state.unmatched = list(unmatched.values())
    state.updated_at = now
    store.save_polymarket(state)
    report.usage = usage
    return report


def _unmatched(
    cand: StoredCandidate,
    episode_id: str,
    prior: UnmatchedCandidate | None,
    seen: list[str],
    now: str,
    rationale: str | None = None,
) -> UnmatchedCandidate:
    merged = list(dict.fromkeys([*(prior.seen_market_ids if prior else []), *seen]))
    return UnmatchedCandidate(
        prediction_id=cand.prediction_id,
        episode_id=episode_id,
        question=cand.question,
        hosts_answer=cand.hosts_answer,
        implied_probability=cand.implied_probability,
        attempts=(prior.attempts if prior else 0) + 1,
        last_attempt_at=now,
        seen_market_ids=merged,
        search_keywords=list(cand.search_keywords),
        last_judge_rationale=rationale if rationale is not None else (prior.last_judge_rationale if prior else None),
    )
