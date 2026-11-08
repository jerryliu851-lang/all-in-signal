"""Deterministic SYNTHETIC dataset for previewing the dashboard (``allin build-site --sample-data``).

Nothing here is real: episode ids, claims, outcomes, market prices and Polymarket slugs are made
up to exercise every view (all statuses, categories, confidence buckets, stance types, repeated
tickers, a private company, recurring themes with shifting stances, flagged divergence and a
resolved market). The output carries ``synthetic=True`` and is never written to ``data/``.
"""

from __future__ import annotations

import random
from typing import Any

from ..config import Settings
from ..models import (
    Chapter,
    EpisodeAnalysis,
    EpisodeIndex,
    EpisodeMeta,
    HostScorecard,
    IndexEntry,
    KeyTake,
    LLMUsage,
    MacroTheme,
    MarketMatch,
    PolymarketState,
    Prediction,
    QuoteVerificationSummary,
    RunRecord,
    Scorecard,
    Stats,
    StockImplication,
    StoredCandidate,
    Timeframe,
    UnmatchedCandidate,
)
from ..util import youtube_url
from .build import SiteData, derive_ledger

SEED = 20261107
AS_OF = "2026-11-07"

CH, SA, FR, JA = "Chamath Palihapitiya", "David Sacks", "David Friedberg", "Jason Calacanis"
GERSTNER, BAKER = "Guest: Brad Gerstner", "Guest: Gavin Baker"

EPISODES: list[dict[str, Any]] = [
    {
        "video_id": "SAMPLE-ep01", "published_at": "2026-10-17", "duration": 6120,
        "title": "[SAMPLE] Fed pivot doubts, the AI capex arms race, robotaxi reality check",
        "guests": ["Brad Gerstner"],
        "chapters": [(0, "Bestie intros"), (380, "Will the Fed blink?"), (1620, "AI capex arms race"),
                     (3010, "Robotaxi reality check"), (4300, "Stablecoin bill"), (5400, "Science corner")],
    },
    {
        "video_id": "SAMPLE-ep02", "published_at": "2026-10-24", "duration": 5880,
        "title": "[SAMPLE] Bond vigilantes return, China chip controls, the SpaceX tender",
        "guests": [],
        "chapters": [(0, "Intros"), (300, "Bond vigilantes and the 10-year"), (1500, "China export controls"),
                     (2700, "SpaceX tender"), (3900, "Shutdown politics"), (4900, "Energy and gas")],
    },
    {
        "video_id": "SAMPLE-ep03", "published_at": "2026-10-31", "duration": 5640,
        "title": "[SAMPLE] Nvidia earnings preview, the energy crunch, cut or hold in December",
        "guests": ["Gavin Baker"],
        "chapters": [(0, "Bestie banter"), (420, "Nvidia earnings preview"), (1500, "The energy crunch"),
                     (2700, "Fed: cut or hold?"), (3900, "Private markets"), (4800, "Science corner")],
    },
]

# (episode index, speaker, stance, attribution, claim, category, direction,
#  (timeframe text, date, inferred), bucket, stated confidence, status, timestamp)
PREDICTIONS: list[tuple] = [
    (0, CH, "own_view", "high", "The Fed will not cut rates at the December 2026 FOMC meeting.", "macro", "no",
     ("December 2026 FOMC", "2026-12-10", False), "high", "I'd bet on it", "pending", 412),
    (0, SA, "own_view", "high", "Nvidia data-center revenue will beat consensus for the October quarter.",
     "company-specific", "up", ("next earnings report", "2026-11-19", True), "medium", "probably", "pending", 1688),
    (0, FR, "own_view", "medium", "A major US utility signs a small modular reactor deal before year end.", "tech", "yes", ("by end of 2026", "2026-12-31", False), "low", "could happen", "pending",
     1902),
    (0, JA, "own_view", "high", "No new city gets robotaxi service without safety monitors by mid-October.", "company-specific", "no", ("by October 15, 2026", "2026-10-15", True), "high",
     "guaranteed", "resolved_correct", 3044),
    (0, CH, "devils_advocate", "medium", "Bitcoin trades above $150k before the end of October.", "crypto", "up",
     ("before end of October 2026", "2026-10-31", False), "medium", "likely", "resolved_incorrect", 3520),
    (0, SA, "own_view", "high", "The Senate passes the stablecoin market-structure bill before Halloween.",
     "politics", "yes", ("before October 31, 2026", "2026-10-31", False), "high", "it's done", "resolved_correct",
     4321),
    (0, FR, "reported_view", "medium", "Analysts expect global AI capex to exceed $600B in 2027.", "AI", "up",
     ("in 2027", "2027-12-31", True), "unstated", None, "pending", 1750),
    (0, JA, "own_view", "high", "OpenAI announces a consumer hardware device before the end of 2026.", "AI", "yes",
     ("by end of 2026", "2026-12-31", True), "unstated", None, "pending", 2210),
    (0, GERSTNER, "own_view", "medium", "The IPO window stays open through Q4 with at least three $10B+ listings.",
     "markets", "up", ("Q4 2026", "2026-12-31", False), "medium", "I think", "pending", 2480),
    (0, CH, "own_view", "high", "Oracle stock closes above $300 by October 24.", "markets", "up",
     ("by October 24, 2026", "2026-10-24", False), "medium", "probably", "resolved_incorrect", 2050),
    (1, CH, "own_view", "high", "The 10-year Treasury yield hits 5% before the end of November.", "macro", "up",
     ("by end of November 2026", "2026-11-30", False), "medium", "60%", "pending", 340),
    (1, SA, "own_view", "high", "The US tightens AI chip export controls on China again within 60 days.",
     "politics", "yes", ("within 60 days", "2026-12-23", True), "high", "100%", "pending", 1530),
    (1, FR, "own_view", "high", "The Q3 GDP advance estimate shows growth above 2.5%.", "macro", "up",
     ("Q3 GDP advance estimate", "2026-10-29", False), "medium", "probably", "resolved_correct", 880),
    (1, JA, "own_view", "medium", "Apple announces an acquisition of an AI model company before year end.",
     "company-specific", "yes", ("by end of 2026", "2026-12-31", True), "low", "wouldn't be surprised",
     "pending", 1810),
    (1, CH, "own_view", "high", "SpaceX's tender values the company above $400B.", "company-specific", "up",
     ("this tender round", "2026-11-01", True), "high", "no doubt", "resolved_correct", 2744),
    (1, SA, "reported_view", "high", "Wall Street strategists expect two more Fed cuts by mid-2027.", "macro",
     "down", ("by mid-2027", "2027-06-30", True), "unstated", None, "pending", 420),
    (1, SA, "own_view", "high", "The government shutdown ends before November.", "politics", "yes",
     ("before November 1, 2026", "2026-11-01", False), "medium", "likely", "resolved_incorrect", 3950),
    (1, FR, "own_view", "high", "US natural gas prices are higher in January than today on data-center demand.",
     "markets", "up", ("by January 2027", "2027-01-31", True), "high", "very confident", "pending", 4950),
    (1, JA, "own_view", "medium", "Tesla shares close the year below where they are today.", "markets", "down",
     ("by end of 2026", "2026-12-31", False), "medium", "I think", "pending", 3100),
    (1, "Unknown", "own_view", "low", "At least one hyperscaler cuts its 2027 capex guidance this earnings cycle.",
     "AI", "down", ("this earnings cycle", "2026-11-01", True), "low", "maybe", "pending", 2100),
    (1, CH, "own_view", "medium", "A crypto exchange announces a US bank charter before November 1.", "crypto",
     "yes", ("before November 1, 2026", "2026-11-01", False), "low", "could", "unresolvable", 3300),
    (2, CH, "own_view", "high", "Nvidia guides next-quarter revenue above $60B.", "company-specific", "up",
     ("next earnings report", "2026-11-19", False), "high", "I'd bet the house", "pending", 470),
    (2, SA, "own_view", "high", "The Fed cuts rates at the December 2026 FOMC meeting.", "macro", "yes",
     ("December 2026 FOMC", "2026-12-10", False), "medium", "more likely than not", "pending", 2730),
    (2, FR, "own_view", "high", "The next IEA report revises data-center power demand forecasts upward.", "tech",
     "up", ("next IEA report", "2027-01-31", True), "medium", "probably", "pending", 1540),
    (2, JA, "own_view", "high", "Anthropic raises at a valuation above $300B before year end.", "AI", "up",
     ("by end of 2026", "2026-12-31", True), "high", "lock it in", "pending", 3960),
    (2, JA, "devils_advocate", "medium", "Bond yields fall sharply as the economy rolls over.", "macro", "down",
     ("next six months", "2027-04-30", True), "low", "maybe", "pending", 2900),
    (2, BAKER, "own_view", "high", "Semiconductor stocks outperform the S&P 500 over the next 12 months.",
     "markets", "up", ("next 12 months", "2027-10-31", True), "medium", "I think", "pending", 900),
    (2, FR, "own_view", "high", "Brent crude stays below $80 through year end.", "markets", "down",
     ("through end of 2026", "2026-12-31", False), "high", "very likely", "pending", 1800),
    (2, FR, "own_view", "medium", "Another cultivated-meat product gets FDA clearance within six months.", "other",
     "yes", ("within six months", "2027-04-30", True), "low", "could", "pending", 4870),
]

# (episode index, speaker, attribution, stance, summary, quote, timestamp, quote status)
KEY_TAKES: list[tuple] = [
    (0, CH, "high", "own_view", "Sticky services inflation leaves the Fed no room to cut in December.",
     "There is no December cut, the data won't let them.", 412, "verified"),
    (0, SA, "high", "own_view", "AI capex is a multi-year arms race, and Nvidia is the arms dealer.",
     "Nvidia is selling the picks and shovels to everyone.", 1688, "verified"),
    (0, JA, "medium", "own_view", "Robotaxi timelines keep slipping because the long tail is brutal.",
     "The last one percent is ninety percent of the work.", 3044, "flagged"),
    (0, FR, "high", "reported_view", "Sell-side models now put 2027 AI capex north of $600B.",
     "The Street is now modeling six hundred billion.", 1750, "verified"),
    (1, CH, "high", "own_view", "Bond vigilantes are back and the 10-year is heading to 5%.",
     "The bond market is telling you something.", 340, "verified"),
    (1, SA, "high", "own_view", "New chip controls will hit Nvidia's China revenue hard.",
     "China revenue for Nvidia goes to zero.", 1530, "verified"),
    (1, CH, "high", "own_view", "The SpaceX tender proves private markets still pay up for category leaders.",
     "Category kings get paid, public or private.", 2744, "verified"),
    (1, FR, "medium", "own_view", "Data-center load is quietly tightening the natural gas market.",
     "Gas is the hidden AI trade nobody talks about.", 4950, "flagged"),
    (2, CH, "high", "own_view", "Nvidia's guide will reset the whole semiconductor complex higher.",
     "This print resets every semi multiple.", 470, "verified"),
    (2, SA, "high", "own_view", "Softening labor data makes a December cut more likely than not.",
     "The labor data gives them cover to cut.", 2730, "verified"),
    (2, CH, "high", "own_view", "Chamath still expects the Fed to hold despite the labor print.",
     "I still think they hold.", 2790, "verified"),
    (2, FR, "high", "own_view", "Power, not chips, is now the binding constraint on AI buildout.",
     "Power is the bottleneck, not silicon.", 1540, "verified"),
    (2, JA, "medium", "devils_advocate", "Jason argues the bear case: the economy is rolling over.",
     "Let me take the other side here.", 2900, "none"),
    (2, BAKER, "medium", "own_view", "Semis outperform for another year as inference demand compounds.",
     "Inference demand compounds every single quarter.", 900, "verified"),
    (2, JA, "high", "own_view", "Anthropic's next round will clear $300B.",
     "That round is getting done above three hundred.", 3960, "verified"),
]

# (episode index, tickers, company, is_private, sector, direction, timeframe, speaker, attribution, ts, claim,
#  rationale, note)
STOCKS: list[tuple] = [
    (0, ["NVDA"], "Nvidia", False, "semiconductors", "bullish", "next 12 months", SA, "high", 1688,
     "AI capex keeps accelerating.", "Hyperscaler capex flows straight to GPU demand.", None),
    (0, ["SMH"], None, False, "semiconductors", "bullish", "multi-year", CH, "high", 1700,
     "The AI buildout lifts the whole chip complex.", "Broad semis exposure captures the capex wave.", None),
    (0, ["TSLA"], "Tesla", False, "autos", "bearish", "next 6 months", JA, "medium", 3044,
     "Robotaxi timelines keep slipping.", "Valuation leans on autonomy that keeps getting pushed out.", None),
    (0, ["TLT"], None, False, "rates", "bearish", "next 3 months", CH, "high", 412,
     "No December cut.", "Higher-for-longer rates pressure long-duration Treasuries.", None),
    (1, ["TLT"], None, False, "rates", "bearish", "by end of November", CH, "high", 340,
     "The 10-year heads to 5%.", "Rising long yields mean falling long-bond prices.", None),
    (1, ["NVDA"], "Nvidia", False, "semiconductors", "bearish", "next 2 quarters", SA, "high", 1530,
     "New export controls on China.", "A tighter China ban removes a revenue stream.", None),
    (1, ["SMH"], None, False, "semiconductors", "neutral", "next 6 months", FR, "medium", 1560,
     "Export controls cut both ways for chipmakers.", "Lost China sales offset by domestic reshoring.", None),
    (1, [], "SpaceX", True, "aerospace", "bullish", "this tender round", CH, "high", 2744,
     "SpaceX tender above $400B.", "Starlink cash flow supports a step-up valuation.",
     "Private company; no liquid public proxy."),
    (1, ["TSLA"], "Tesla", False, "autos", "bearish", "by end of 2026", JA, "medium", 3100,
     "Tesla closes the year lower.", "Auto margins compress while robotaxi stays in pilot.", None),
    (2, ["NVDA"], "Nvidia", False, "semiconductors", "bullish", "this earnings cycle", CH, "high", 470,
     "Nvidia guides above $60B.", "A beat-and-raise resets estimates higher.", None),
    (2, ["SMH", "NVDA"], None, False, "semiconductors", "bullish", "next 12 months", BAKER, "medium", 900,
     "Semis outperform the S&P 500.", "Inference demand compounds faster than supply.", None),
    (2, ["TLT"], None, False, "rates", "bullish", "next 6 months", JA, "medium", 2900,
     "Yields fall as the economy rolls over (devil's advocate).", "Recession fears pull long yields down.", None),
    (2, ["TSLA"], "Tesla", False, "autos", "neutral", "next 6 months", SA, "low", 3300,
     "Tesla is range-bound until robotaxi data arrives.", "No catalyst either way near term.", None),
    (2, ["VST", "CEG"], None, False, "power", "bullish", "multi-year", FR, "high", 1540,
     "Power is the AI bottleneck.", "Owners of firm generation capacity capture AI load growth.", None),
    (2, [], "Anthropic", True, "AI", "bullish", "by end of 2026", JA, "high", 3960,
     "Anthropic raises above $300B.", "Revenue growth supports a higher private mark.",
     "Private company; no public ticker."),
    (2, [], "Regional grid operators", False, "utilities", "bullish", "multi-year", FR, "medium", 1620,
     "Interconnection queues become the scarce asset.", "Grid access gates every new data center.",
     "No single liquid instrument tracks interconnection capacity."),
]

# (episode index, theme, stance summary, speakers, ts)
THEMES: list[tuple] = [
    (0, "rates", "Hold camp: Chamath says sticky inflation rules out a December cut.", [CH, SA], 412),
    (1, "rates", "Hawkish turn: bond vigilantes back, Chamath calls 5% on the 10-year.", [CH], 340),
    (2, "rates", "Split: Sacks now expects a December cut while Chamath still says hold.", [SA, CH], 2730),
    (0, "ai capex", "Bullish: a multi-year arms race with Nvidia as the arms dealer.", [SA, FR], 1620),
    (1, "ai capex", "Doubts creep in: someone floats a hyperscaler capex cut.", [JA, FR], 2100),
    (2, "ai capex", "Back to bullish, but power, not chips, is the constraint.", [FR, CH], 1500),
    (1, "china", "Hawkish: another round of chip export controls is coming.", [SA], 1500),
    (2, "china", "Calmer: controls are priced in; focus shifts to domestic supply.", [SA, FR], 1600),
    (1, "energy", "Gas is the hidden AI trade as data-center load climbs.", [FR], 4900),
    (2, "energy", "Energy crunch: firm power is the scarcest input for AI.", [FR, JA], 1500),
    (0, "regulation", "Stablecoin bill looks done; crypto regulation finally clears.", [SA], 4300),
    (1, "fiscal", "Shutdown drags on; fiscal brinkmanship spooks the long end.", [SA, CH], 3900),
    (2, "ipo market", "Private marks keep climbing; IPO window still open for leaders.", [JA, BAKER], 3900),
]

# (prediction index, question, hosts answer, implied p(yes), market yes, closed, resolution outcome, end date,
#  match confidence)
MATCHES: list[tuple] = [
    (0, "Will the Fed cut rates at the December 2026 FOMC meeting?", "no", 0.15, 0.62, False, None,
     "2026-12-10", "high"),
    (22, "Will the Fed cut rates at the December 2026 FOMC meeting?", "yes", 0.65, 0.62, False, None,
     "2026-12-10", "high"),
    (10, "Will the 10-year Treasury yield hit 5% by November 30, 2026?", "yes", 0.60, 0.18, False, None,
     "2026-11-30", "medium"),
    (5, "Will the stablecoin market-structure bill pass the Senate by October 31?", "yes", 0.85, 1.0, True, "Yes",
     "2026-10-31", "high"),
    (27, "Will Brent crude close above $80 in 2026?", "no", 0.20, 0.27, False, None, "2026-12-31", "medium"),
]
UNMATCHED_IDX: list[tuple[int, float | None]] = [(7, None), (13, 0.3), (24, 0.8)]


def _pid(video_id: str, n: int) -> str:
    return f"{video_id}-p{n:02d}"


def _stats(items: list[Prediction]) -> Stats:
    correct = sum(1 for p in items if p.status == "resolved_correct")
    incorrect = sum(1 for p in items if p.status == "resolved_incorrect")
    resolved = correct + incorrect
    return Stats(
        total=len(items),
        resolved=resolved,
        correct=correct,
        incorrect=incorrect,
        pending=sum(1 for p in items if p.status == "pending"),
        unresolvable=sum(1 for p in items if p.status == "unresolvable"),
        accuracy=round(correct / resolved, 4) if resolved else None,
    )


def _host_card(name: str, items: list[Prediction]) -> HostScorecard:
    cats = sorted({p.category for p in items})
    buckets = [b for b in ("high", "medium", "low", "unstated") if any(p.confidence_bucket == b for p in items)]
    return HostScorecard(
        host=name,
        overall=_stats(items),
        by_category={c: _stats([p for p in items if p.category == c]) for c in cats},
        calibration={b: _stats([p for p in items if p.confidence_bucket == b]) for b in buckets},
    )


def sample_scorecard(analyses: list[EpisodeAnalysis], hosts: tuple[str, ...], updated_at: str) -> Scorecard:
    """own_view only, matching the counting rule in DECISIONS.md."""
    own = [p for a in analyses for p in a.predictions if p.stance_type == "own_view"]
    others = sorted({p.speaker for p in own if p.speaker not in hosts})
    return Scorecard(
        updated_at=updated_at,
        hosts={h: _host_card(h, [p for p in own if p.speaker == h]) for h in hosts},
        others={o: _host_card(o, [p for p in own if p.speaker == o]) for o in others},
    )


def generate_sample(settings: Settings) -> SiteData:
    rng = random.Random(SEED)
    analyses: list[EpisodeAnalysis] = []
    ep_preds: dict[int, list[Prediction]] = {i: [] for i in range(len(EPISODES))}
    flat: list[Prediction] = []
    counters = {i: 0 for i in range(len(EPISODES))}

    for row in PREDICTIONS:
        (ei, speaker, stance, attr, claim, cat, direction, tf, bucket, stated, status, ts) = row
        ep = EPISODES[ei]
        counters[ei] += 1
        resolved = status in ("resolved_correct", "resolved_incorrect", "unresolvable")
        p = Prediction(
            id=_pid(ep["video_id"], counters[ei]),
            episode_id=ep["video_id"],
            speaker=speaker,
            attribution_confidence=attr,
            stance_type=stance,
            claim=claim,
            category=cat,
            direction=direction,
            timeframe=Timeframe(text=tf[0], date=tf[1], inferred=tf[2]),
            resolution_criteria=f"Check the public record on or after {tf[1]}: {claim}",
            stated_confidence=stated,
            confidence_bucket=bucket,
            quote=None if stance == "reported_view" else " ".join(claim.rstrip(".").split()[:9]),
            timestamp_seconds=ts,
            status=status,
            resolution_source="manual" if resolved else None,
            resolution_note="Synthetic sample outcome." if resolved else None,
            resolved_at="2026-11-01" if resolved else None,
            quote_verified=rng.choice(["verified", "verified", "flagged", "none"]),
        )
        ep_preds[ei].append(p)
        flat.append(p)

    for mi, row in enumerate(MATCHES):
        flat[row[0]].polymarket_market_id = f"sample-mkt-{mi + 1}"
        if row[5] and row[6]:
            flat[row[0]].resolution_source = "polymarket"

    for ei, ep in enumerate(EPISODES):
        vid = ep["video_id"]
        chapters = ep["chapters"]
        takes = [
            KeyTake(speaker=s, attribution_confidence=a, stance_type=st, summary=su, quote=q,
                    timestamp_seconds=t, quote_verified=qv)
            for (i, s, a, st, su, q, t, qv) in KEY_TAKES if i == ei
        ]
        stocks = [
            StockImplication(id=f"{vid}-s{n:02d}", claim=c, tickers=tk, company=co, is_private=pr, sector=se,
                             direction=d, timeframe=tf, rationale=ra, speaker=sp, attribution_confidence=at,
                             timestamp_seconds=t, note=no)
            for n, (i, tk, co, pr, se, d, tf, sp, at, t, c, ra, no) in enumerate(
                [r for r in STOCKS if r[0] == ei], start=1)
        ]
        themes = [MacroTheme(theme=th, stance_summary=su, speakers=sp, timestamp_seconds=t)
                  for (i, th, su, sp, t) in THEMES if i == ei]
        cands = []
        for row in MATCHES:
            p = flat[row[0]]
            if p.episode_id == vid:
                cands.append(StoredCandidate(prediction_id=p.id, question=row[1], hosts_answer=row[2],
                                             implied_probability=row[3]))
        for idx, prob in UNMATCHED_IDX:
            p = flat[idx]
            if p.episode_id == vid:
                cands.append(StoredCandidate(prediction_id=p.id, question=f"Will it happen: {p.claim}",
                                             hosts_answer="yes", implied_probability=prob))
        verified = sum(1 for t in takes if t.quote_verified == "verified")
        flagged = sum(1 for t in takes if t.quote_verified == "flagged")
        analyses.append(EpisodeAnalysis(
            episode=EpisodeMeta(
                video_id=vid, title=ep["title"], published_at=ep["published_at"], url=youtube_url(vid),
                duration_seconds=ep["duration"],
                chapters=[Chapter(start_time=float(s), end_time=float(chapters[k + 1][0]) if k + 1 < len(chapters)
                                  else float(ep["duration"]), title=t) for k, (s, t) in enumerate(chapters)],
            ),
            hosts_present=list(settings.hosts) if ei != 1 else [CH, SA, FR, JA],
            guests=list(ep["guests"]),
            key_takes=takes,
            predictions=ep_preds[ei],
            stock_implications=stocks,
            macro_themes=themes,
            polymarket_candidates=cands,
            analyzed_at=f"{ep['published_at']}T18:00:00Z",
            model="sample",
            backend="sample",
            usage=LLMUsage(input_tokens=rng.randint(28_000, 36_000), output_tokens=rng.randint(9_000, 13_000),
                           calls=1),
            quote_verification=QuoteVerificationSummary(checked=len(takes), verified=verified, flagged=flagged),
        ))

    matches: list[MarketMatch] = []
    for mi, (pi, q, ans, implied, market, closed, outcome, end, conf) in enumerate(MATCHES):
        p = flat[pi]
        div = round(abs(implied - market) * 100, 1)
        matches.append(MarketMatch(
            prediction_id=p.id, episode_id=p.episode_id, candidate_question=q, market_id=f"sample-mkt-{mi + 1}",
            market_question=q, event_title=q.removeprefix("Will ").rstrip("?"), slug=f"sample-market-{mi + 1}",
            closed=closed, active=not closed, end_date=end,
            outcome_prices={"Yes": market, "No": round(1 - market, 4)},
            hosts_answer=ans, implied_probability=implied, market_yes_probability=market,
            divergence_points=div, flagged_divergence=div > settings.divergence_flag_points,
            resolution_outcome=outcome, match_confidence=conf,
            match_rationale="Synthetic sample match.", matched_at=f"{AS_OF}T12:00:00Z",
            last_checked_at=f"{AS_OF}T12:00:00Z",
        ))
    unmatched = [
        UnmatchedCandidate(prediction_id=flat[i].id, episode_id=flat[i].episode_id,
                           question=f"Will it happen: {flat[i].claim}", hosts_answer="yes",
                           implied_probability=prob, attempts=1, last_attempt_at=f"{AS_OF}T12:00:00Z")
        for i, prob in UNMATCHED_IDX
    ]
    polymarket = PolymarketState(updated_at=f"{AS_OF}T12:00:00Z", matches=matches, unmatched=unmatched)

    analyses.sort(key=lambda a: a.episode.published_at, reverse=True)
    index = EpisodeIndex(updated_at=f"{AS_OF}T12:00:00Z", episodes={
        a.episode.video_id: IndexEntry(
            video_id=a.episode.video_id, title=a.episode.title, published_at=a.episode.published_at,
            url=a.episode.url, duration_seconds=a.episode.duration_seconds, status="analyzed",
            discovered_at=f"{a.episode.published_at}T12:00:00Z", transcript_attempts=1,
            transcript_source="sample", analyzed_at=a.analyzed_at, chapters=a.episode.chapters,
        ) for a in analyses
    })
    runs = [RunRecord(started_at=f"{AS_OF}T11:00:00Z", finished_at=f"{AS_OF}T11:20:00Z", command="sample",
                      analyzed=[a.episode.video_id for a in analyses], notes=["synthetic sample data"])]
    return SiteData(
        analyses=analyses,
        index=index,
        ledger=derive_ledger(analyses, polymarket, AS_OF),
        scorecard=sample_scorecard(analyses, settings.hosts, f"{AS_OF}T12:00:00Z"),
        polymarket=polymarket,
        runs=runs,
        synthetic=True,
    )
