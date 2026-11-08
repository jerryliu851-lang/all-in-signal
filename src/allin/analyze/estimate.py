"""``allin estimate``: token and API-equivalent cost estimate for pending episodes (no model call)."""

from __future__ import annotations

import json

from ..config import PRICE_PER_MTOK, Settings
from ..models import EstimateEpisode, EstimateReport
from ..store import Store
from ..util import estimate_tokens
from .analyzer import log
from .chunking import chunk_transcript, needs_chunking
from .pipeline import episode_meta
from .prompts import chunk_scope_note, render_analysis_prompt, render_consolidation_prompt

NOTE = (
    "Token counts are a chars/{cpt} heuristic (the project has no token-counting endpoint), covering "
    "system + user prompt input and an assumed {out} output tokens per model call; extended thinking "
    "and prompt caching are not modelled. Cost is the API-equivalent list price for {model} "
    "(${pin}/MTok in, ${pout}/MTok out). With the Max subscription token the real cost is quota, not dollars."
)


def _cost(inp: int, out: int) -> float:
    return round(inp * PRICE_PER_MTOK["input"] / 1e6 + out * PRICE_PER_MTOK["output"] / 1e6, 4)


def _scope(store: Store, video_ids: list[str] | None) -> list[str]:
    if video_ids:
        return list(dict.fromkeys(video_ids))
    return [
        vid
        for vid, e in store.load_index().episodes.items()
        if e.status == "transcribed" and store.load_analysis(vid) is None
    ]


def estimate_episodes(store: Store, settings: Settings, video_ids: list[str] | None = None) -> EstimateReport:
    """Estimate input/output tokens and API-equivalent cost per episode and in total."""
    cpt = settings.chars_per_token
    index = store.load_index()
    episodes: list[EstimateEpisode] = []
    skipped: list[str] = []
    for vid in _scope(store, video_ids):
        entry = index.episodes.get(vid)
        transcript = store.load_normalized_transcript(vid)
        if entry is None or transcript is None:
            skipped.append(vid)
            continue
        meta = episode_meta(entry, transcript)
        chunked = needs_chunking(transcript, settings)
        if not chunked:
            system, user = render_analysis_prompt(meta, transcript, settings)
            inp = estimate_tokens(system + "\n" + user, cpt)
            out = settings.est_output_tokens_per_episode
        else:
            chunks = chunk_transcript(transcript, settings)
            inp = 0
            for i, ch in enumerate(chunks):
                system, user = render_analysis_prompt(meta, ch, settings, scope_note=chunk_scope_note(i, len(chunks), ch))
                inp += estimate_tokens(system + "\n" + user, cpt)
            # Consolidation input: its prompt plus every chunk's output (assumed full size each).
            system, user = render_consolidation_prompt([], meta, settings)
            inp += estimate_tokens(system + "\n" + user, cpt) + len(chunks) * settings.est_output_tokens_per_episode
            out = (len(chunks) + 1) * settings.est_output_tokens_per_episode
        episodes.append(
            EstimateEpisode(
                video_id=vid,
                title=meta.title,
                words=transcript.word_count,
                est_input_tokens=inp,
                est_output_tokens=out,
                est_cost_usd_equivalent=_cost(inp, out),
                chunked=chunked,
            )
        )
        log(f"estimate {vid}: in~{inp} out~{out} chunked={chunked}")
    total_in = sum(e.est_input_tokens for e in episodes)
    total_out = sum(e.est_output_tokens for e in episodes)
    note = NOTE.format(
        cpt=cpt, out=settings.est_output_tokens_per_episode, model=settings.model,
        pin=PRICE_PER_MTOK["input"], pout=PRICE_PER_MTOK["output"],
    )
    if skipped:
        note += f" Skipped (not in index or no transcript): {json.dumps(skipped)}."
    return EstimateReport(
        episodes=episodes,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost_usd_equivalent=round(sum(e.est_cost_usd_equivalent for e in episodes), 4),
        note=note,
    )
