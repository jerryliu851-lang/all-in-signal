# All-In Signal

**Live dashboard:** https://jerryliu851-lang.github.io/all-in-signal/ · **Repo:** https://github.com/jerryliu851-lang/all-in-signal

All-In Signal turns every full All-In Podcast episode into a structured ledger: key takes, predictions, stock implications, macro themes, and matches against Polymarket markets. Each prediction is later resolved (by Polymarket or by hand in `resolutions.yml`) and rolled into a per-host scorecard. It runs on a GitHub Actions cron with zero servers and publishes a static dashboard to GitHub Pages.

**Research tool, not a trading system. Nothing here places trades.**

## Architecture

```mermaid
flowchart TD
    cron[GitHub Actions cron, every 6 h] --> yt[YouTube playlist]
    yt --> ingest["ingest: yt-dlp listing, youtube-transcript-api, yt-dlp subs fallback"]
    ingest --> norm[normalize transcript]
    norm --> opus["Claude Opus claude-opus-5-5 via Claude Code CLI, JSON-schema structured output"]
    opus --> qv[quote verification]
    qv --> data[(data/ JSON)]
    data --> pm[Polymarket Gamma matching]
    pm --> res["resolution: Polymarket + resolutions.yml"]
    res --> sc[scorecard + ledger]
    sc --> site[static site]
    site --> pages[GitHub Pages]
```

Idle runs (no new episode) make no analysis calls, write no data and trigger no deploy. The only model calls outside a new episode are small Polymarket re-judgments of previously unmatched candidates, at most once every 7 days per candidate and only when Gamma lists a market not seen before.

## Hosts and attribution

Four hosts: Chamath Palihapitiya, David Sacks, David Friedberg, Jason Calacanis. Guests are recorded as `Guest: <name>`, otherwise `Unknown`. The captions have no speaker labels, so attribution is inferred from context, and every item carries an `attribution_confidence` of high, medium or low. Each prediction also has a `stance_type`: `own_view`, `reported_view` or `devils_advocate`. Only `own_view` counts toward accuracy; the rest stay in the ledger.

## Setup

1. Fork or clone `github.com/jerryliu851-lang/all-in-signal` (must be public for free Pages) and run `uv sync`.
2. Create a long-lived token locally with `claude setup-token`, then store it:
   `gh secret set CLAUDE_CODE_OAUTH_TOKEN -R jerryliu851-lang/all-in-signal`
3. Enable Pages with source "GitHub Actions" (Settings > Pages), or:
   `gh api -X POST repos/jerryliu851-lang/all-in-signal/pages -f build_type=workflow`
4. Run the **verify-runner** workflow and read its summary (YouTube access, tooling, token present).
5. Run the **backfill** workflow in `estimate` mode, read the summary, then run it in `run` mode.
6. From then on the **pipeline** cron (`17 */6 * * *`) takes over.

| Command | What it does |
|---|---|
| `uv run allin ingest` | Poll the playlist, fetch and normalize transcripts |
| `uv run allin estimate` | Token / API-equivalent estimate for pending episodes |
| `uv run allin analyze` | Opus analysis of transcribed episodes |
| `uv run allin match` | Judge Polymarket candidates |
| `uv run allin resolve` | Resolve predictions, rebuild scorecard and ledger |
| `uv run allin build-site --out _site` | Assemble the static dashboard |
| `uv run allin backfill --count N --estimate-only` | Ingest N episodes and print estimate |
| `uv run allin backfill --count N --confirm` | Backfill for real (spends quota) |
| `uv run allin verify-runner` | Check YouTube, Gamma and the CLI from this machine |
| `uv run allin schema` | Check the JSON schema is current (`--write` to update) |
| `uv run pytest` | Test suite |

## Why a Max-subscription token instead of an API key

The project runs on Jerry's Claude Max plan: the model is called through the Claude Code CLI authenticated with `CLAUDE_CODE_OAUTH_TOKEN`, so cost is subscription quota, not dollars. Trade-offs: the token expires and must be re-created with `claude setup-token`; there is no dollar meter, so the estimate reports token counts plus an API-equivalent USD figure taken from the CLI envelope; and the Claude Code harness adds roughly 12k cached system-prompt tokens per call. In Actions the code refuses to run analysis if the token is empty.

## Storage decision

Data is committed JSON under `data/` behind a `Store` interface. JSON diffs and merges cleanly in git and is served straight from GitHub Pages, which SQLite blobs would not be. Swapping in Supabase later means writing one module that implements `Store`.

## Data files

| Path | Shape | Purpose |
|---|---|---|
| `data/index.json` | `EpisodeIndex` | Every discovered episode, status, retry counters, exclusions |
| `data/transcripts/<id>.raw.json` | `{source, snippets: [TranscriptSnippet]}` | Raw captions as fetched |
| `data/transcripts/<id>.normalized.json` | `NormalizedTranscript` | Cleaned, paragraphed transcript with timestamps |
| `data/episodes/<id>.json` | `EpisodeAnalysis` | Takes, predictions, stock implications, macro themes, usage, quote verification |
| `data/polymarket.json` | `PolymarketState` | Matches and unmatched candidates |
| `data/predictions.json` | `PredictionsLedger` | Consumer-facing ledger with stable schema |
| `data/scorecard.json` | `Scorecard` | Per-host and per-category accuracy and calibration |
| `data/runs.json` | `[RunRecord]` | Run log with usage, most recent last |
| `schema/episode_analysis.schema.json` | JSON Schema | Structured-output schema, checked in CI |
| `_site/` (built, not committed) | static HTML/JS/CSS | Dashboard published to Pages |

## Cost per episode

<!-- COST_TABLE_START -->
Measured on the 2026-11-07 episode (93 min, 17.1k words) with `claude-opus-5-5` at effort `high`, single pass:

| Item | Measured |
|---|---|
| Input tokens (transcript + prompt + Claude Code harness prefix) | 39,362 (written to cache; later calls read ~12k of it from cache) |
| Output tokens | 28,926, of which 14,579 thinking |
| Wall time | 236 s |
| API-equivalent cost | $0.89 (at $4 / $20 per MTok) |
| Polymarket judge calls | a few hundred tokens each at effort `low`, one per candidate (7 candidates on this episode) |
| Run with nothing new | no analysis calls; rare small re-judge calls for unmatched candidates (see Architecture) |

So a 10-episode backfill is roughly 390k input + 290k output tokens, about $9 API-equivalent, and about 40 minutes of sequential runner time. With the Max-subscription token the real cost is subscription quota: split the backfill into two batches (`count=5`, then `count=10`) if you want to spread it across two 5-hour windows.
<!-- COST_TABLE_END -->

## Editing resolutions.yml

Manual resolutions live in `resolutions.yml` and are read on every run. Manual entries beat Polymarket resolutions. Prediction ids look like `<video_id>-p<nn>` and are found in `data/predictions.json` or on the dashboard ledger.

```yaml
resolutions:
  - prediction_id: 2v8b062QzTI-p03
    outcome: correct          # correct | incorrect | unresolvable
    note: "Fed cut 25bp at the December 2026 FOMC."
    source: https://www.federalreserve.gov/newsevents/pressreleases.htm
    resolved_on: 2026-12-16
```

## Known limitations
- **Scheduled workflows pause after 60 days of repository inactivity.** GitHub disables cron triggers on repos with no activity for 60 days; commits made by the pipeline itself may not count. If the dashboard stops updating, open the Actions tab and re-enable the `pipeline` workflow or push any commit.

- No speaker labels in captions, so attribution is inferred and can be wrong.
- YouTube may block cloud IPs. Run verify-runner first; set `ALLIN_YT_PROXY` if blocked; a yt-dlp subtitle fallback exists. Failed episodes are marked `transcript_unavailable` with a retry counter, never silently skipped.
- The YouTube RSS feed is dead (404), so polling uses yt-dlp.
- Implied probabilities are model estimates, not market data.
- Quote verification drops quotes that cannot be found in the transcript.
- The Max token expires and must be re-created.
- Pages requires a public repo, so the transcripts are public.

## Future: trading-bot integration (not built)

`data/predictions.json` and `data/scorecard.json` are designed as inputs for a future bot. A consumer needs: a stable `schema_version`; stable prediction ids; the `status` field (`pending`, `resolved_correct`, `resolved_incorrect`, `unresolvable`); `resolution_source`, `resolution_note`, `resolved_at`; confidence fields (`attribution_confidence`, `confidence_bucket`, `stated_confidence`, `implied_probability`); Polymarket fields and divergence; and `timeframe.date`. No trading integration exists in this repo and none is planned here.

## Future: price-based scoring (hook only)

`allin.scoring.price_scoring.score_stock_calls_by_price` raises `NotImplementedError` by design. A provider would need to supply `price_provider(ticker, date_iso) -> float` (adjusted close on or before the date), a parser from the free-text `timeframe` to a horizon in days, and a tolerance band for neutral calls. Output would be one record per (implication, ticker) with entry/exit price, forward return, and agreement with `direction`; calls whose horizon has not elapsed stay pending.

## Disclaimer

For research and educational use only. This is not financial advice and places no trades. The hosts' views are theirs, not the author's. Transcripts are auto-captions and may contain errors.

## License

MIT
