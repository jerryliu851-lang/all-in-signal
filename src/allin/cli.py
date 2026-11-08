"""Command-line entry point: ``allin <command>`` (or ``python -m allin``).

This file is the CONTRACT between work units. Each stage package must expose exactly these
functions with these signatures (see the lazy imports below):

    allin.ingest.pipeline.run_ingest(store, settings, *, limit=None, backfill=False) -> IngestReport
    allin.analyze.pipeline.run_analyze(store, settings, backend, *, video_ids=None) -> AnalyzeReport
    allin.analyze.estimate.estimate_episodes(store, settings, video_ids=None) -> EstimateReport
    allin.polymarket.pipeline.run_match(store, settings, backend, *, video_ids=None) -> MatchReport
    allin.scoring.pipeline.run_resolve(store, settings) -> ResolveReport
    allin.site.build.build_site(store, settings, out_dir, *, sample_data=False) -> Path
    allin.ingest.verify.verify_runner(settings) -> dict   (checks printed; raises SystemExit(1) on hard failure)

Stages that call the model receive ``backend`` (``allin.llm.LLMBackend``) and must route every
model call through ``backend.complete_json``. Nothing else in the codebase may invoke the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .models import LLMUsage, RunRecord
from .store import JsonFileStore
from .util import now_iso, write_github_output


def _print_json(obj) -> None:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _store(settings: Settings) -> JsonFileStore:
    return JsonFileStore(settings.data_dir)


def _backend(settings: Settings):
    from .llm import get_backend

    return get_backend(settings)


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def cmd_ingest(args, settings: Settings) -> int:
    from .ingest.pipeline import run_ingest

    report = run_ingest(_store(settings), settings, limit=args.limit, backfill=args.backfill)
    _print_json(report)
    pending = [vid for vid, e in _store(settings).load_index().episodes.items() if e.status == "transcribed"]
    write_github_output(
        new_episodes=len(report.new_transcribed),
        new_episode_ids=report.new_transcribed,
        pending_analysis=len(pending),
    )
    return 0


def cmd_estimate(args, settings: Settings) -> int:
    from .analyze.estimate import estimate_episodes

    report = estimate_episodes(_store(settings), settings, video_ids=args.ids or None)
    _print_json(report)
    return 0


def cmd_analyze(args, settings: Settings) -> int:
    from .analyze.pipeline import run_analyze

    started = now_iso()
    report = run_analyze(_store(settings), settings, _backend(settings), video_ids=args.ids or None, force=args.force)
    _print_json(report)
    _store(settings).append_run(
        RunRecord(started_at=started, finished_at=now_iso(), command="analyze", analyzed=report.analyzed,
                  usage=report.usage, notes=[f"failed:{k}:{v[:120]}" for k, v in report.failed.items()])
    )
    write_github_output(analyzed=len(report.analyzed))
    return 1 if (report.failed and not report.analyzed) else 0


def cmd_match(args, settings: Settings) -> int:
    from .llm import LazyBackend
    from .polymarket.pipeline import run_match

    started = now_iso()
    # Lazy: an idle tick with nothing to judge needs no credentials; a real judge call still fails loudly.
    report = run_match(_store(settings), settings, LazyBackend(settings), video_ids=args.ids or None)
    _print_json(report)
    if report.usage.calls:
        _store(settings).append_run(
            RunRecord(started_at=started, finished_at=now_iso(), command="match", usage=report.usage,
                      notes=[f"matched:{len(report.matched)}", f"unmatched:{len(report.unmatched)}"])
        )
    return 0


def cmd_resolve(args, settings: Settings) -> int:
    from .scoring.pipeline import run_resolve

    report = run_resolve(_store(settings), settings)
    _print_json(report)
    return 0


def cmd_build_site(args, settings: Settings) -> int:
    from .site.build import build_site

    out = build_site(_store(settings), settings, Path(args.out), sample_data=args.sample_data)
    print(f"site built at {out}")
    return 0


def cmd_schema(args, settings: Settings) -> int:
    from .schema import schema_matches_models, write_schema

    if args.write:
        write_schema(settings.schema_path)
        print(f"wrote {settings.schema_path}")
        return 0
    ok = schema_matches_models(settings.schema_path)
    print("schema up to date" if ok else "schema OUT OF DATE: run `allin schema --write`")
    return 0 if ok else 1


def cmd_verify_runner(args, settings: Settings) -> int:
    from .ingest.verify import verify_runner

    result = verify_runner(settings)
    _print_json(result)
    return 0


def cmd_backfill(args, settings: Settings) -> int:
    """Ingest the N most recent full episodes, then (unless --estimate-only) analyze, match, resolve, build."""
    from .analyze.estimate import estimate_episodes
    from .ingest.pipeline import run_ingest

    store = _store(settings)
    started = now_iso()
    ingest = run_ingest(store, settings, limit=args.count, backfill=True)
    transcribed = [e for e in store.load_index().episodes.values() if e.status == "transcribed"]
    transcribed.sort(key=lambda e: (e.published_at or "", e.video_id), reverse=True)
    pending = [e.video_id for e in transcribed[: args.count]]  # newest N, so batches can be split across runs
    estimate = estimate_episodes(store, settings, video_ids=pending)
    print("=== BACKFILL ESTIMATE ===")
    _print_json(estimate)
    if args.estimate_only:
        print("estimate-only: stopping before any model call")
        return 0
    if not args.confirm:
        print("Refusing to spend: re-run with --confirm after reviewing the estimate above.")
        return 2

    from .analyze.pipeline import run_analyze
    from .polymarket.pipeline import run_match
    from .scoring.pipeline import run_resolve

    backend = _backend(settings)
    usage = LLMUsage()
    analyze = run_analyze(store, settings, backend, video_ids=pending)
    usage = usage.add(analyze.usage)
    match = run_match(store, settings, backend, video_ids=analyze.analyzed)
    usage = usage.add(match.usage)
    resolve = run_resolve(store, settings)
    store.append_run(
        RunRecord(started_at=started, finished_at=now_iso(), command="backfill",
                  new_episodes=ingest.new_transcribed, analyzed=analyze.analyzed, usage=usage,
                  notes=[f"matched:{len(match.matched)}", f"resolved:{len(resolve.resolved)}"]
                  + [f"failed:{k}:{v[:120]}" for k, v in analyze.failed.items()])
    )
    _print_json({"ingest": ingest.model_dump(mode="json"), "analyze": analyze.model_dump(mode="json"),
                 "match": match.model_dump(mode="json"), "resolve": resolve.model_dump(mode="json")})
    return 1 if (analyze.failed and not analyze.analyzed) else 0


# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="allin", description="All-In Signal pipeline (research tool; places no trades)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest", help="poll the playlist, fetch + normalize transcripts for new full episodes")
    s.add_argument("--limit", type=int, default=None, help="only consider the N newest playlist entries")
    s.add_argument("--backfill", action="store_true", help="treat the newest --limit entries as the backfill set")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("estimate", help="token / API-equivalent cost estimate for pending episodes (no model call)")
    s.add_argument("--ids", nargs="*", default=None)
    s.set_defaults(func=cmd_estimate)

    s = sub.add_parser("analyze", help="run the Opus analysis on transcribed, unanalyzed episodes")
    s.add_argument("--ids", nargs="*", default=None)
    s.add_argument("--force", action="store_true",
                   help="re-analyze episodes that already have an analysis (prediction ids may shift; "
                        "their Polymarket matches are dropped and resolutions.yml entries may orphan)")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("match", help="match polymarket candidates against Gamma markets")
    s.add_argument("--ids", nargs="*", default=None)
    s.set_defaults(func=cmd_match)

    s = sub.add_parser("resolve", help="resolve pending predictions, rebuild scorecard + ledger")
    s.set_defaults(func=cmd_resolve)

    s = sub.add_parser("build-site", help="assemble the static dashboard into --out")
    s.add_argument("--out", default="_site")
    s.add_argument("--sample-data", action="store_true", help="use synthetic sample data (local preview only)")
    s.set_defaults(func=cmd_build_site)

    s = sub.add_parser("schema", help="write or check schema/episode_analysis.schema.json")
    s.add_argument("--write", action="store_true")
    s.set_defaults(func=cmd_schema)

    s = sub.add_parser("verify-runner", help="check YouTube listing, captions, Gamma and the claude CLI from this machine")
    s.set_defaults(func=cmd_verify_runner)

    s = sub.add_parser("backfill", help="one-time backfill of the N most recent full episodes (gated on --confirm)")
    s.add_argument("--count", type=int, default=None)
    s.add_argument("--estimate-only", action="store_true")
    s.add_argument("--confirm", action="store_true", help="actually spend model quota")
    s.set_defaults(func=cmd_backfill)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if getattr(args, "count", None) is None and args.command == "backfill":
        args.count = settings.backfill_count
    try:
        return int(args.func(args, settings) or 0)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
