"""Ingest pipeline: discover full episodes, classify, fetch + normalize transcripts."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from ..config import Settings
from ..models import ExcludedEntry, IndexEntry, IngestReport
from ..store.base import Store
from ..util import now_iso, youtube_url
from .normalize import normalize
from .transcripts import TranscriptUnavailable, fetch_transcript
from .youtube import PlaylistEntry, classify_episode, fetch_video_meta, list_playlist_entries, rss_fast_path

RETRY_AFTER = timedelta(days=7)
# Exclusion reasons that describe a temporary state of the video, re-evaluated on every listing.
TRANSIENT_EXCLUSIONS = frozenset({"live_or_upcoming", "duration_unknown"})


def _log(msg: str) -> None:
    print(f"[ingest] {msg}", file=sys.stderr, flush=True)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).astimezone(UTC)
    except ValueError:
        return None


def _eligible(entry: IndexEntry, settings: Settings) -> bool:
    if entry.status not in ("discovered", "transcript_unavailable"):
        return False
    if entry.transcript_attempts < settings.max_transcript_attempts:
        return True
    last = _parse_ts(entry.last_attempt_at)
    return last is None or datetime.now(UTC) - last > RETRY_AFTER


def run_ingest(store: Store, settings: Settings, *, limit: int | None = None, backfill: bool = False) -> IngestReport:
    """Poll the playlist, record new episodes, and fetch transcripts for eligible ones."""
    report = IngestReport()
    index = store.load_index()

    list_limit = limit if limit and not backfill else (max(limit * 3, settings.listing_limit) if limit else settings.listing_limit)
    entries = list_playlist_entries(settings, limit=list_limit)
    _log(f"listed {len(entries)} entries")
    known = {e.video_id for e in entries}

    rss_ids = None
    if limit is None and not backfill:  # explicit --limit / backfill runs trust the playlist listing only
        try:
            rss_ids = rss_fast_path(settings)
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"rss: error {type(exc).__name__}, skipped")
        if rss_ids is None:
            report.notes.append("rss: unavailable, skipped")
    if rss_ids:
        for vid in rss_ids:
            if vid not in known and vid not in index.episodes and vid not in index.excluded:
                entries.append(PlaylistEntry(video_id=vid, url=youtube_url(vid), in_full_episode_playlist=False))
                known.add(vid)

    report.discovered = [e.video_id for e in entries]
    scope: list[str] = []  # ids eligible for transcription this run in backfill mode
    full_seen = 0
    cutoff = False  # forward-only: set once the newest already-known playlist episode is reached

    for entry in entries:
        vid = entry.video_id
        if backfill and limit and full_seen >= limit:
            break
        if cutoff and entry.in_full_episode_playlist and vid not in index.episodes:
            continue  # older than the newest episode we track: never reach back past the backfill
        try:
            if vid in index.episodes:
                full_seen += 1
                scope.append(vid)
                if not backfill and not cutoff and entry.in_full_episode_playlist:
                    # The listing is newest-first, so every playlist entry after this one is older.
                    cutoff = True
                    report.notes.append(f"forward-only: ignoring playlist entries older than {vid}")
                continue
            ex = index.excluded.get(vid)
            if ex is not None and ex.reason == "not_in_full_episode_playlist" and entry.in_full_episode_playlist:
                del index.excluded[vid]  # playlist caught up with a video first seen via RSS: reclassify
                ex = None
            if ex is not None and ex.reason in TRANSIENT_EXCLUSIONS:
                # A premiere / live stream / not-yet-processed video becomes a normal episode later:
                # never exclude it permanently; reclassify every time it is listed.
                del index.excluded[vid]
                ex = None
            if ex is not None:
                ex.seen_at = now_iso()
                report.excluded.append(ex)
                continue
            meta = None
            if entry.duration_seconds is not None and entry.duration_seconds < settings.min_full_episode_seconds:
                ok, reason = classify_episode(entry, None, settings)
            else:
                meta = fetch_video_meta(vid, settings)
                ok, reason = classify_episode(entry, meta, settings)
            if ok:
                title = (meta.title if meta and meta.title else entry.title) or vid
                index.episodes[vid] = IndexEntry(
                    video_id=vid,
                    title=title,
                    published_at=meta.published_at if meta else None,
                    url=(meta.url if meta and meta.url else entry.url) or youtube_url(vid),
                    duration_seconds=(meta.duration_seconds if meta and meta.duration_seconds is not None else entry.duration_seconds),
                    status="discovered",
                    discovered_at=now_iso(),
                    chapters=meta.chapters if meta else [],
                )
                _log(f"{vid}: new full episode ({reason}) {title[:60]}")
                full_seen += 1
                scope.append(vid)
            else:
                title = (meta.title if meta and meta.title else entry.title) or vid
                dur = meta.duration_seconds if meta and meta.duration_seconds is not None else entry.duration_seconds
                ex = ExcludedEntry(video_id=vid, title=title, duration_seconds=dur, reason=reason, seen_at=now_iso())
                index.excluded[vid] = ex
                report.excluded.append(ex)
                _log(f"{vid}: excluded ({reason})")
        except Exception as exc:  # noqa: BLE001 - one bad entry never aborts the run
            report.notes.append(f"{vid}: listing/meta error {type(exc).__name__}: {str(exc)[:200]}")
            _log(f"{vid}: meta error {exc}")

    index.updated_at = now_iso()
    store.save_index(index)

    todo = [e for e in index.episodes.values() if _eligible(e, settings) and (not backfill or e.video_id in scope)]
    todo.sort(key=lambda e: e.published_at or "", reverse=True)
    for ep in todo:
        vid = ep.video_id
        if ep.transcript_attempts > 0:
            report.retried.append(vid)
        _log(f"{vid}: fetching transcript (attempt {ep.transcript_attempts + 1})")
        try:
            snippets, source, generated = fetch_transcript(vid, settings)
            norm = normalize(vid, snippets, source, generated)
            store.save_raw_transcript(vid, snippets, source)
            store.save_normalized_transcript(norm)
            ep.status = "transcribed"
            ep.transcript_source = source
            ep.last_error = None
            report.new_transcribed.append(vid)
            _log(f"{vid}: transcribed via {source}, {norm.word_count} words")
        except TranscriptUnavailable as exc:
            ep.status = "transcript_unavailable"
            ep.last_error = exc.reason
            _log(f"{vid}: transcript unavailable")
        except Exception as exc:  # noqa: BLE001
            ep.status = "transcript_unavailable"
            ep.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"{vid}: unexpected error {ep.last_error}")
        ep.transcript_attempts += 1
        ep.last_attempt_at = now_iso()
        index.updated_at = now_iso()
        store.save_index(index)

    report.unavailable = [e.video_id for e in index.episodes.values() if e.status == "transcript_unavailable"]
    return report
