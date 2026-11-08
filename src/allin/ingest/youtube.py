"""YouTube listing, metadata and episode classification (yt-dlp based; RSS is an optional fast path)."""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..config import Settings
from ..models import Chapter
from ..util import youtube_url


def _log(msg: str) -> None:
    print(f"[ingest] {msg}", file=sys.stderr, flush=True)


class PlaylistEntry(BaseModel):
    """One row of a flat playlist/channel listing."""

    video_id: str
    title: str = ""
    duration_seconds: int | None = None
    url: str = ""
    in_full_episode_playlist: bool = True
    live_status: str | None = None


class VideoMeta(BaseModel):
    """Full metadata for one video (from yt-dlp extract_info, download=False)."""

    video_id: str
    title: str
    published_at: str | None = None  # YYYY-MM-DD
    duration_seconds: int | None = None
    chapters: list[Chapter] = Field(default_factory=list)
    url: str = ""
    automatic_caption_langs: list[str] = Field(default_factory=list)
    subtitle_langs: list[str] = Field(default_factory=list)
    live_status: str | None = None


def _ydl_opts(settings: Settings, **extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
    if settings.yt_proxy:
        opts["proxy"] = settings.yt_proxy
    opts.update(extra)
    return opts


def _to_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _entries_from_info(info: dict[str, Any], in_playlist: bool) -> list[PlaylistEntry]:
    out: list[PlaylistEntry] = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        vid = str(e["id"])
        out.append(
            PlaylistEntry(
                video_id=vid,
                title=e.get("title") or "",
                duration_seconds=_to_int(e.get("duration")),
                url=e.get("url") or youtube_url(vid),
                in_full_episode_playlist=in_playlist,
                live_status=e.get("live_status"),
            )
        )
    return out


def list_playlist_entries(settings: Settings, limit: int | None = None) -> list[PlaylistEntry]:
    """List the newest entries of the full-episode playlist (fallback: the channel /videos tab).

    Playlist-sourced entries carry ``in_full_episode_playlist=True``; channel-sourced ones False.
    """
    import yt_dlp

    opts = _ydl_opts(settings, extract_flat="in_playlist")
    if limit:
        opts["playlistend"] = limit
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(settings.playlist_url, download=False)
        entries = _entries_from_info(info or {}, True)
        if entries:
            return entries
        _log("playlist listing returned no entries; falling back to channel /videos")
    except Exception as exc:  # noqa: BLE001 - any yt-dlp failure triggers the fallback
        _log(f"playlist listing failed ({type(exc).__name__}: {exc}); falling back to channel /videos")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(settings.channel_videos_url, download=False)
    return _entries_from_info(info or {}, False)


def rss_fast_path(settings: Settings) -> list[str] | None:
    """Newest video ids from the channel Atom feed, or None on any non-200 / parse error."""
    try:
        r = httpx.get(settings.rss_url, timeout=5.0, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        _log(f"rss: request failed ({type(exc).__name__}), skipped")
        return None
    if r.status_code != 200:
        _log(f"rss: HTTP {r.status_code}, skipped")
        return None
    try:
        root = ET.fromstring(r.content)
        ns = {"yt": "http://www.youtube.com/xml/schemas/2015"}
        ids = [e.text for e in root.iter(f"{{{ns['yt']}}}videoId") if e.text]
    except ET.ParseError:
        _log("rss: parse error, skipped")
        return None
    return ids or None


def _fmt_date(upload_date: str | None) -> str | None:
    if upload_date and re.fullmatch(r"\d{8}", str(upload_date)):
        s = str(upload_date)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def meta_from_info(info: dict[str, Any], video_id: str) -> VideoMeta:
    """Build a VideoMeta from a yt-dlp info dict (pure; used by tests)."""
    chapters = [
        Chapter(start_time=float(c["start_time"]), end_time=(float(c["end_time"]) if c.get("end_time") is not None else None), title=c.get("title") or "")
        for c in (info.get("chapters") or [])
        if c.get("start_time") is not None
    ]
    auto = info.get("automatic_captions")
    subs = info.get("subtitles")
    auto_langs = list(auto) if isinstance(auto, dict) else list(info.get("automatic_captions_langs") or [])
    sub_langs = list(subs) if isinstance(subs, dict) else list(info.get("subtitles_langs") or [])
    return VideoMeta(
        video_id=video_id,
        title=info.get("title") or "",
        published_at=_fmt_date(info.get("upload_date")),
        duration_seconds=_to_int(info.get("duration")),
        chapters=chapters,
        url=info.get("webpage_url") or youtube_url(video_id),
        automatic_caption_langs=auto_langs,
        subtitle_langs=sub_langs,
        live_status=info.get("live_status"),
    )


def fetch_video_meta(video_id: str, settings: Settings) -> VideoMeta:
    """Fetch full metadata (no media download) for one video."""
    import yt_dlp

    opts = _ydl_opts(settings, noplaylist=True, ignore_no_formats_error=True, check_formats=False)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url(video_id), download=False)
    return meta_from_info(info or {}, video_id)


def classify_episode(entry: PlaylistEntry, meta: VideoMeta | None, settings: Settings) -> tuple[bool, str]:
    """Decide whether a video is a full roundtable episode. Returns (is_full, reason)."""
    title = (meta.title if meta and meta.title else entry.title) or ""
    live = (meta.live_status if meta and meta.live_status else entry.live_status) or ""
    if live in ("is_live", "is_upcoming", "post_live"):
        return False, "live_or_upcoming"
    duration = meta.duration_seconds if meta and meta.duration_seconds is not None else entry.duration_seconds
    if duration is None:
        return False, "duration_unknown"
    floor = settings.min_full_episode_seconds
    if duration < floor:
        return False, f"duration_{duration}s_below_{floor}s_floor"
    if entry.in_full_episode_playlist:
        return True, "in_full_episode_playlist"
    if re.search(settings.legacy_title_pattern, title):
        return True, "legacy_episode_title"
    return False, "not_in_full_episode_playlist"
