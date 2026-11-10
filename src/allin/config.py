"""Runtime settings. Everything has a default; env vars prefixed ALLIN_ override.

Verified facts baked in here (2026-11-07):
- Channel id for youtube.com/@allin is UCESLZhusAkFfsNsApnjF_Cg (canonical link on the handle page).
- Full roundtable episodes live in the channel playlist "All-In Podcast" PLn5MTSAqaf8peDZQ57QkJBzewJU1aUokl.
- YouTube's Atom RSS endpoint returned 404 for every channel tested, so it is only an optional fast path.
- Current Opus model string is claude-opus-5-5 (platform.claude.com models page).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HOSTS: tuple[str, ...] = (
    "Chamath Palihapitiya",
    "David Sacks",
    "David Friedberg",
    "Jason Calacanis",
)
HOST_SHORT: dict[str, str] = {
    "Chamath Palihapitiya": "Chamath",
    "David Sacks": "Sacks",
    "David Friedberg": "Friedberg",
    "Jason Calacanis": "Jason",
}

# API-equivalent list prices for claude-opus-5-5, USD per million tokens (for reporting only;
# with the Max subscription token the real cost is quota, not dollars).
PRICE_PER_MTOK: dict[str, float] = {
    "input": 4.00,
    "output": 20.00,
    "cache_read": 0.20,
    "cache_write": 5.00,
}


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # --- YouTube source ---
    channel_id: str = "UCESLZhusAkFfsNsApnjF_Cg"
    channel_handle: str = "allin"
    full_episodes_playlist_id: str = "PLn5MTSAqaf8peDZQ57QkJBzewJU1aUokl"
    min_full_episode_seconds: int = 45 * 60
    legacy_title_pattern: str = r"\bE\d{2,4}\b"
    max_transcript_attempts: int = 8
    yt_proxy: str | None = None
    listing_limit: int = 30  # how many newest playlist entries to look at per poll

    # --- LLM ---
    llm_backend: str = "claude-code"  # "claude-code" | "fake"
    model: str = "claude-opus-5-5"
    analysis_effort: str = "high"
    match_effort: str = "low"
    claude_bin: str = "claude"
    llm_timeout_seconds: int = 1800
    max_budget_usd_per_call: float = 4.0
    chunk_threshold_tokens: int = 120_000
    chunk_target_tokens: int = 60_000
    chunk_overlap_seconds: int = 120
    chars_per_token: float = 3.6
    est_output_tokens_per_episode: int = 12_000
    est_match_tokens_per_candidate: int = 2_500
    # A cited attribution cue must sit this close to its item to support "high". Measured over the
    # 10-episode backfill: 99% of speaker turns last under 200 s and the longest is 443 s.
    attribution_window_before_seconds: int = 480
    attribution_window_after_seconds: int = 120

    # --- Polymarket ---
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    divergence_flag_points: float = 25.0
    match_retry_days: int = 7
    match_top_n: int = 12

    # --- Pipeline ---
    backfill_count: int = 10

    # --- Paths ---
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    prompts_dir: Path = ROOT / "prompts"
    schema_path: Path = ROOT / "schema" / "episode_analysis.schema.json"
    resolutions_path: Path = ROOT / "resolutions.yml"
    site_src_dir: Path = ROOT / "site"
    site_out_dir: Path = ROOT / "_site"

    hosts: tuple[str, ...] = HOSTS
    host_short: dict[str, str] = field(default_factory=lambda: dict(HOST_SHORT))

    @property
    def in_ci(self) -> bool:
        return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"

    @property
    def playlist_url(self) -> str:
        return f"https://www.youtube.com/playlist?list={self.full_episodes_playlist_id}"

    @property
    def channel_videos_url(self) -> str:
        return f"https://www.youtube.com/@{self.channel_handle}/videos"

    @property
    def rss_url(self) -> str:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(_env("ALLIN_DATA_DIR", str(ROOT / "data")))
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        return cls(
            llm_backend=_env("ALLIN_LLM_BACKEND", cls.llm_backend),
            model=_env("ALLIN_MODEL", cls.model),
            analysis_effort=_env("ALLIN_ANALYSIS_EFFORT", cls.analysis_effort),
            match_effort=_env("ALLIN_MATCH_EFFORT", cls.match_effort),
            claude_bin=_env("ALLIN_CLAUDE_BIN", cls.claude_bin),
            llm_timeout_seconds=_env_int("ALLIN_LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds),
            max_budget_usd_per_call=_env_float("ALLIN_MAX_BUDGET_USD_PER_CALL", cls.max_budget_usd_per_call),
            chunk_threshold_tokens=_env_int("ALLIN_CHUNK_THRESHOLD_TOKENS", cls.chunk_threshold_tokens),
            backfill_count=_env_int("ALLIN_BACKFILL_COUNT", cls.backfill_count),
            yt_proxy=os.environ.get("ALLIN_YT_PROXY") or None,
            data_dir=data_dir,
        )
