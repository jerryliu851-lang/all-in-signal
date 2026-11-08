"""Typed contracts shared by every work unit.

Two families live here:

1. ``*Out`` models: exactly what the LLM must return for one episode (``AnalysisOutput``).
   Their JSON Schema (``allin.schema.analysis_output_schema``) is handed to the model as the
   structured-output schema, so every field here must be REQUIRED (no defaults) and every
   object must forbid extra keys.
2. Stored models: what the pipeline persists (``EpisodeAnalysis``, ``NormalizedTranscript``,
   ``EpisodeIndex``, ``PolymarketState``, ``Scorecard``, ``PredictionsLedger``) plus run reports.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------

AttributionConfidence = Literal["high", "medium", "low"]
StanceType = Literal["own_view", "reported_view", "devils_advocate"]
Category = Literal["macro", "markets", "politics", "tech", "AI", "crypto", "company-specific", "other"]
StockDirection = Literal["bullish", "bearish", "neutral"]
PredictionStatus = Literal["pending", "resolved_correct", "resolved_incorrect", "unresolvable"]
ConfidenceBucket = Literal["high", "medium", "low", "unstated"]
YesNo = Literal["yes", "no"]
QuoteVerificationStatus = Literal["verified", "flagged", "dropped", "none"]
EpisodeStatus = Literal["discovered", "transcript_unavailable", "transcribed", "analyzed"]
ManualOutcome = Literal["correct", "incorrect", "unresolvable"]

SPEAKER_DESC = (
    "Who said it: one of the four hosts' full names ('Chamath Palihapitiya', 'David Sacks', "
    "'David Friedberg', 'Jason Calacanis'), 'Guest: <name>' when a guest is identifiable, "
    "otherwise 'Unknown'. Captions carry no speaker labels, so infer from context and set "
    "attribution_confidence honestly."
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# LLM output (structured-output schema). ALL FIELDS REQUIRED. NO DEFAULTS.
# --------------------------------------------------------------------------------------


class Timeframe(StrictModel):
    text: str = Field(
        description=(
            "Horizon as the speaker framed it, or the inferred horizon, e.g. 'by end of 2026', "
            "'within 12 months', 'before the midterms'."
        )
    )
    date: str | None = Field(
        description=(
            "Resolution date as YYYY-MM-DD when a concrete date can be stated (explicit or inferred); "
            "null if no date can be pinned."
        )
    )
    inferred: bool = Field(description="true when the horizon was not stated explicitly and you inferred it.")


class KeyTakeOut(StrictModel):
    speaker: str = Field(description=SPEAKER_DESC)
    attribution_confidence: AttributionConfidence
    stance_type: StanceType = Field(
        description=(
            "own_view = the speaker's own position; reported_view = the speaker summarizing someone "
            "else's view; devils_advocate = the speaker arguing a side they do not hold."
        )
    )
    summary: str = Field(description="One sentence stating the position or argument.")
    quote: str = Field(
        description=(
            "Verbatim supporting quote copied exactly from the transcript text, under 15 words. "
            "Never paraphrase; a quote that is not in the transcript will be dropped."
        )
    )
    timestamp_seconds: int = Field(description="Start time in seconds of the transcript paragraph where this is said.")


class PredictionOut(StrictModel):
    speaker: str = Field(description=SPEAKER_DESC)
    attribution_confidence: AttributionConfidence
    stance_type: StanceType = Field(
        description="own_view, reported_view (summarizing someone else's forecast) or devils_advocate."
    )
    claim: str = Field(
        description=(
            "The forward-looking claim in one or two sentences, normalized into a checkable statement "
            "while keeping the speaker's meaning."
        )
    )
    category: Category
    direction: str | None = Field(
        description=(
            "Direction if applicable, e.g. 'up', 'down', 'higher', 'lower', 'yes', 'no', 'win', 'lose'; "
            "null when the claim is not directional."
        )
    )
    timeframe: Timeframe
    resolution_criteria: str = Field(
        description=(
            "Concrete criteria a reader could later use to judge this true or false, naming the "
            "observable: a price level, an event, an official statistic, an election outcome."
        )
    )
    stated_confidence: str | None = Field(
        description="The speaker's own confidence language if any, e.g. 'I'd bet on it', '70%', 'probably'; null if none."
    )
    confidence_bucket: ConfidenceBucket = Field(
        description=(
            "Bucket of the speaker's stated confidence: high (certain, would bet, guaranteed, 80%+), "
            "medium (likely, probably, 50-80%), low (maybe, could, wouldn't be surprised), unstated."
        )
    )
    quote: str | None = Field(
        description="Verbatim supporting quote copied exactly from the transcript, under 15 words, or null."
    )
    timestamp_seconds: int = Field(description="Start time in seconds of the transcript paragraph where this is said.")


class StockImplicationOut(StrictModel):
    claim: str = Field(description="The underlying claim this implication is drawn from, one sentence.")
    tickers: list[str] = Field(
        description=(
            "Liquid US tickers or ETFs (e.g. 'NVDA', 'SMH', 'TLT'). Empty list for private companies or "
            "when no liquid instrument fits."
        )
    )
    company: str | None = Field(description="Company or asset name when the claim is about one; null otherwise.")
    is_private: bool = Field(description="true when the company is private (no public ticker).")
    sector: str | None = Field(description="Sector label, e.g. 'semiconductors', 'software', 'energy', 'rates'; null if n/a.")
    direction: StockDirection
    timeframe: str = Field(description="Horizon in words, e.g. 'next 12 months', 'this earnings cycle', 'multi-year'.")
    rationale: str = Field(description="One or two sentences on why the claim implies this direction.")
    speaker: str = Field(description=SPEAKER_DESC)
    attribution_confidence: AttributionConfidence
    timestamp_seconds: int
    note: str | None = Field(
        description="Required when tickers is empty: why no ticker (private company, no liquid proxy, etc.); else null."
    )


class MacroThemeOut(StrictModel):
    theme: str = Field(
        description=(
            "Short canonical lowercase theme label reused across episodes, e.g. 'rates', 'ai capex', "
            "'energy', 'fiscal', 'trade', 'regulation', 'ai safety', 'elections', 'china', 'ipo market'."
        )
    )
    stance_summary: str = Field(description="One line on where the group (or named host) landed on this theme.")
    speakers: list[str] = Field(description="Speakers who drove this theme, same naming rules as elsewhere.")
    timestamp_seconds: int = Field(description="Start time in seconds of the first substantial discussion of the theme.")


class PolymarketCandidateOut(StrictModel):
    prediction_index: int = Field(description="0-based index into the predictions array this candidate derives from.")
    question: str = Field(
        description=(
            "The prediction rephrased as a yes/no question in prediction-market style, e.g. "
            "'Will the Fed cut rates at the December 2026 FOMC meeting?'"
        )
    )
    search_keywords: list[str] = Field(
        description=(
            "2 to 4 short search queries a prediction-market search box would understand, most specific "
            "first, e.g. ['Anthropic IPO', 'Anthropic IPO 2026'] or ['Fed rate cut December', 'FOMC December']. "
            "Entity names plus the key noun; no full sentences, no 'Will'."
        )
    )
    hosts_answer: YesNo = Field(description="The answer the speaker's view implies.")
    implied_probability: float | None = Field(
        description=(
            "Best estimate (0 to 1) of the probability of 'yes' the speaker implied, derived from their "
            "confidence language; null if it cannot be inferred."
        )
    )


class AnalysisOutput(StrictModel):
    """Exactly what the model returns for one episode (or one chunk)."""

    hosts_present: list[str] = Field(description="Which of the four hosts are present, by full name.")
    guests: list[str] = Field(description="Guest names when identifiable; empty if none.")
    key_takes: list[KeyTakeOut] = Field(description="The 8 to 15 most important positions or arguments.")
    predictions: list[PredictionOut] = Field(
        description="Every explicit or strongly implied forward-looking claim. When unsure, include with low confidence."
    )
    stock_implications: list[StockImplicationOut]
    macro_themes: list[MacroThemeOut]
    polymarket_candidates: list[PolymarketCandidateOut] = Field(
        description="Predictions that look like something a prediction market would list, as yes/no questions."
    )


# --------------------------------------------------------------------------------------
# Stored analysis
# --------------------------------------------------------------------------------------


class Chapter(StrictModel):
    start_time: float
    end_time: float | None = None
    title: str


class EpisodeMeta(StrictModel):
    video_id: str
    title: str
    published_at: str  # YYYY-MM-DD
    url: str
    duration_seconds: int
    chapters: list[Chapter] = Field(default_factory=list)


class LLMUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd_equivalent: float = 0.0
    duration_ms: int = 0
    calls: int = 0

    def add(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
            cost_usd_equivalent=round(self.cost_usd_equivalent + other.cost_usd_equivalent, 6),
            duration_ms=self.duration_ms + other.duration_ms,
            calls=self.calls + other.calls,
        )


class QuoteVerificationSummary(StrictModel):
    checked: int = 0
    verified: int = 0
    flagged: int = 0
    dropped: int = 0


class KeyTake(KeyTakeOut):
    quote_verified: QuoteVerificationStatus = "none"


class Prediction(PredictionOut):
    id: str  # "<video_id>-p<nn>"
    episode_id: str
    status: PredictionStatus = "pending"
    resolution_source: str | None = None  # "polymarket" | "manual" | None
    resolution_note: str | None = None
    resolved_at: str | None = None
    polymarket_market_id: str | None = None
    quote_verified: QuoteVerificationStatus = "none"


class StockImplication(StockImplicationOut):
    id: str  # "<video_id>-s<nn>"


class MacroTheme(MacroThemeOut):
    pass


class StoredCandidate(StrictModel):
    prediction_id: str
    question: str
    hosts_answer: YesNo
    implied_probability: float | None = None
    search_keywords: list[str] = Field(default_factory=list)


class EpisodeAnalysis(StrictModel):
    schema_version: str = "1"
    episode: EpisodeMeta
    hosts_present: list[str]
    guests: list[str]
    key_takes: list[KeyTake]
    predictions: list[Prediction]
    stock_implications: list[StockImplication]
    macro_themes: list[MacroTheme]
    polymarket_candidates: list[StoredCandidate]
    analyzed_at: str
    model: str
    backend: str
    chunked: bool = False
    usage: LLMUsage = Field(default_factory=LLMUsage)
    quote_verification: QuoteVerificationSummary = Field(default_factory=QuoteVerificationSummary)


# --------------------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------------------


class TranscriptSnippet(StrictModel):
    """One raw caption fragment as returned by youtube-transcript-api / yt-dlp."""

    text: str
    start: float
    duration: float


class TranscriptParagraph(StrictModel):
    start: float
    end: float
    text: str


class NormalizedTranscript(StrictModel):
    video_id: str
    language: str = "en"
    source: str  # "youtube-transcript-api" | "yt-dlp" | "podcast-whisper:<model>"
    is_generated: bool = True
    source_detail: str | None = None  # e.g. podcast file name and the podcast-vs-YouTube duration delta
    paragraphs: list[TranscriptParagraph]
    word_count: int

    def full_text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


# --------------------------------------------------------------------------------------
# Episode index (what has been seen / processed)
# --------------------------------------------------------------------------------------


class IndexEntry(StrictModel):
    video_id: str
    title: str
    published_at: str | None = None
    url: str
    duration_seconds: int | None = None
    status: EpisodeStatus = "discovered"
    discovered_at: str
    transcript_attempts: int = 0
    last_attempt_at: str | None = None
    last_error: str | None = None
    transcript_source: str | None = None
    analyzed_at: str | None = None
    matched_at: str | None = None
    chapters: list[Chapter] = Field(default_factory=list)


class ExcludedEntry(StrictModel):
    video_id: str
    title: str
    duration_seconds: int | None = None
    reason: str
    seen_at: str


class EpisodeIndex(StrictModel):
    updated_at: str | None = None
    episodes: dict[str, IndexEntry] = Field(default_factory=dict)
    excluded: dict[str, ExcludedEntry] = Field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Polymarket
# --------------------------------------------------------------------------------------


class MarketMatch(StrictModel):
    prediction_id: str
    episode_id: str
    candidate_question: str
    market_id: str
    market_question: str
    event_title: str | None = None
    slug: str | None = None
    closed: bool
    active: bool
    end_date: str | None = None
    outcome_prices: dict[str, float] = Field(default_factory=dict)  # outcome label -> price (0-1)
    hosts_answer: YesNo
    polarity: Literal["same", "inverse"] = "same"  # inverse: market asks the negation of the candidate
    implied_probability: float | None = None
    market_yes_probability: float | None = None  # in the hosts' question frame (1 - price when inverse)
    divergence_points: float | None = None  # |implied - market| * 100
    flagged_divergence: bool = False
    resolution_outcome: str | None = None  # winning outcome label for closed+resolved markets
    match_confidence: AttributionConfidence
    match_rationale: str
    matched_at: str
    last_checked_at: str


class UnmatchedCandidate(StrictModel):
    prediction_id: str
    episode_id: str
    question: str
    hosts_answer: YesNo
    implied_probability: float | None = None
    attempts: int = 0
    last_attempt_at: str | None = None
    seen_market_ids: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    last_judge_rationale: str | None = None


class PolymarketState(StrictModel):
    updated_at: str | None = None
    matches: list[MarketMatch] = Field(default_factory=list)
    unmatched: list[UnmatchedCandidate] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------


class Stats(StrictModel):
    total: int = 0
    resolved: int = 0
    correct: int = 0
    incorrect: int = 0
    pending: int = 0
    unresolvable: int = 0
    accuracy: float | None = None  # correct / resolved, None when resolved == 0


class HostScorecard(StrictModel):
    host: str
    overall: Stats = Field(default_factory=Stats)
    by_category: dict[str, Stats] = Field(default_factory=dict)
    calibration: dict[str, Stats] = Field(default_factory=dict)  # confidence bucket -> stats


class Scorecard(StrictModel):
    schema_version: str = "1"
    updated_at: str
    counting_rule: str = "own_view predictions only; reported views and devil's advocate excluded"
    hosts: dict[str, HostScorecard] = Field(default_factory=dict)
    others: dict[str, HostScorecard] = Field(default_factory=dict)  # guests / Unknown


class ManualResolution(StrictModel):
    prediction_id: str
    outcome: ManualOutcome
    note: str
    source: str
    resolved_on: str | None = None


class LedgerEntry(Prediction):
    episode_title: str
    episode_url: str
    episode_published_at: str
    youtube_timestamp_url: str
    polymarket_question: str | None = None
    polymarket_yes_probability: float | None = None
    polymarket_divergence_points: float | None = None
    timeframe_passed: bool = False  # timeframe.date is in the past and the prediction is still pending


class PredictionsLedger(StrictModel):
    """Consumer-facing ledger. Stable schema; see README 'future trading-bot integration'."""

    schema_version: str = "1"
    updated_at: str
    count: int
    predictions: list[LedgerEntry]


# --------------------------------------------------------------------------------------
# Run reports (returned by each pipeline stage; serialized to the run log)
# --------------------------------------------------------------------------------------


class IngestReport(StrictModel):
    discovered: list[str] = Field(default_factory=list)  # video ids seen in this poll
    new_transcribed: list[str] = Field(default_factory=list)  # ready for analysis
    excluded: list[ExcludedEntry] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)  # transcript_unavailable after this run
    retried: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AnalyzeReport(StrictModel):
    analyzed: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)
    usage: LLMUsage = Field(default_factory=LLMUsage)


class EstimateEpisode(StrictModel):
    video_id: str
    title: str
    words: int
    est_input_tokens: int
    est_output_tokens: int
    est_cost_usd_equivalent: float
    chunked: bool


class EstimateReport(StrictModel):
    episodes: list[EstimateEpisode]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd_equivalent: float
    note: str


class MatchReport(StrictModel):
    matched: list[str] = Field(default_factory=list)  # prediction ids newly matched
    unmatched: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)  # not retried yet (cooldown / no new markets)
    resolved_from_closed: list[str] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)


class ResolveReport(StrictModel):
    resolved: list[str] = Field(default_factory=list)
    still_pending: int = 0
    scorecard_path: str
    ledger_path: str


class RunRecord(StrictModel):
    started_at: str
    finished_at: str
    command: str
    new_episodes: list[str] = Field(default_factory=list)
    analyzed: list[str] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    notes: list[str] = Field(default_factory=list)
