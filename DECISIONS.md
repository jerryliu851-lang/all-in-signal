# DECISIONS

One line per judgment call that the brief did not specify. Append; never rewrite history.

- 2026-11-07 Poller uses `yt-dlp` flat-playlist listing, not RSS: YouTube's Atom feed (`/feeds/videos.xml?channel_id=`) returned 404 for every channel tested, including a control channel. RSS is still tried first as an optional fast path and ignored on any non-200.
- 2026-11-07 "Full episode" = member of the channel's **All-In Podcast** playlist (`PLn5MTSAqaf8peDZQ57QkJBzewJU1aUokl`) AND duration >= 45 min; legacy `E###` titles are also accepted. Current titles no longer carry `E###`, and the 25-55 min interview videos are not in that playlist.
- 2026-11-07 Analysis backend is the Claude Code CLI in print mode with `--json-schema` structured output, authenticated with Jerry's Max subscription (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`), not the Anthropic SDK + API key. Model pinned to `claude-opus-5-5`. The backend sits behind `allin.llm.LLMBackend`, so an SDK backend is a one-file addition.
- 2026-11-07 Storage = committed JSON under `data/` behind `allin.store.Store`. Chosen over SQLite because JSON diffs and merges in git and is served directly by GitHub Pages; the interface keeps a Supabase swap to one module.
- 2026-11-07 Single-pass analysis when the transcript is under 120k estimated tokens (every All-In roundtable so far is ~25-35k); chunk + consolidate only above the threshold.
- 2026-11-07 Opus 5.5 at low effort also judges Polymarket matches. Unmatched candidates are retried at most every 7 days and only when the Gamma search surfaces market ids not judged before, so idle runs make zero LLM calls.
- 2026-11-07 Repo is public (free GitHub plan requires public for Pages). Normalized transcripts are committed and therefore public.
- 2026-11-07 Scorecard counts only `stance_type == own_view` predictions. Reported views and devil's-advocate items stay in the ledger but are excluded from accuracy.
- 2026-11-07 Cost is reported as token counts plus "API-equivalent USD" from the CLI envelope (`total_cost_usd`). With the Max token the real cost is subscription quota, not dollars.
- 2026-11-07 Dashboard uses liquid-glass styling per the brief, overriding the maintainer's usual no-glassmorphism default.
- 2026-11-07 Prediction ids are `<video_id>-p<nn>` (zero-padded, order of appearance) so they are stable across re-runs of the same episode and human-readable in `resolutions.yml`.
