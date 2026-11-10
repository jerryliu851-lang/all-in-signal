"""Transcript retrieval: youtube-transcript-api, then yt-dlp json3 captions, then podcast audio + Whisper."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

import httpx

from ..config import Settings
from ..models import TranscriptSnippet
from ..util import youtube_url


def _log(msg: str) -> None:
    print(f"[ingest] {msg}", file=sys.stderr, flush=True)


class TranscriptResult(NamedTuple):
    snippets: list[TranscriptSnippet]
    source: str
    is_generated: bool
    source_detail: str | None = None


class TranscriptUnavailable(Exception):
    """No transcript could be fetched; ``reason`` joins the per-backend errors."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_json3(obj: dict[str, Any]) -> list[TranscriptSnippet]:
    """Parse a YouTube json3 caption document into snippets (events[].segs[].utf8)."""
    out: list[TranscriptSnippet] = []
    for ev in obj.get("events") or []:
        segs = ev.get("segs")
        if not segs or "tStartMs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        out.append(
            TranscriptSnippet(
                text=text,
                start=float(ev["tStartMs"]) / 1000.0,
                duration=float(ev.get("dDurationMs") or 0) / 1000.0,
            )
        )
    return out


def _fetch_via_api(video_id: str, settings: Settings) -> tuple[list[TranscriptSnippet], bool]:
    """youtube-transcript-api backend. Prefers manual en, then generated en, then any en-*."""
    from youtube_transcript_api import NoTranscriptFound, YouTubeTranscriptApi

    kwargs: dict[str, Any] = {}
    if settings.yt_proxy:
        from youtube_transcript_api.proxies import GenericProxyConfig

        kwargs["proxy_config"] = GenericProxyConfig(http_url=settings.yt_proxy, https_url=settings.yt_proxy)
    api = YouTubeTranscriptApi(**kwargs)
    tlist = api.list(video_id)
    chosen = None
    for finder in (tlist.find_manually_created_transcript, tlist.find_generated_transcript):
        try:
            chosen = finder(["en"])
            break
        except NoTranscriptFound:
            _log(f"{video_id}: no {finder.__name__} en track")
    if chosen is None:
        for t in tlist:
            if t.language_code.lower().startswith("en"):
                chosen = t
                break
    if chosen is None:
        raise RuntimeError("no English transcript track")
    raw = chosen.fetch().to_raw_data()
    snippets = [TranscriptSnippet(text=r["text"], start=float(r["start"]), duration=float(r.get("duration", 0.0))) for r in raw]
    return snippets, bool(chosen.is_generated)


def _fetch_via_ytdlp(video_id: str, settings: Settings) -> tuple[list[TranscriptSnippet], bool]:
    """yt-dlp fallback: read the json3 caption URL from automatic_captions/subtitles (no media download)."""
    import yt_dlp

    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                            "ignore_no_formats_error": True, "check_formats": False}
    if settings.yt_proxy:
        opts["proxy"] = settings.yt_proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url(video_id), download=False) or {}
    for key, generated in (("subtitles", False), ("automatic_captions", True)):
        tracks = info.get(key) or {}
        for lang in [k for k in ("en", "en-orig") if k in tracks] + sorted(k for k in tracks if k.startswith("en-") and k != "en-orig"):
            for fmt in tracks[lang]:
                if fmt.get("ext") == "json3" and fmt.get("url"):
                    r = httpx.get(fmt["url"], timeout=30.0, follow_redirects=True, proxy=settings.yt_proxy)
                    r.raise_for_status()
                    snippets = parse_json3(r.json())
                    if snippets:
                        return snippets, generated
    raise RuntimeError("no English json3 caption track found")


def _fetch_via_podcast(
    title: str | None, duration_seconds: int | None, settings: Settings
) -> tuple[list[TranscriptSnippet], str, str]:
    """Podcast audio + faster-whisper. Returns (snippets, source, source_detail)."""
    from . import podcast
    from . import whisper_transcribe as W

    if not title and duration_seconds is None:
        raise RuntimeError("podcast fallback needs the episode title or duration")
    items = podcast.load_feed(settings)
    item = podcast.match_feed_item(items, title=title or "", duration_seconds=duration_seconds)
    if item is None:
        raise RuntimeError(f"no podcast feed item matches {title!r} ({duration_seconds}s)")
    model = W.whisper_model_name()
    compute = W.whisper_compute_type()
    fname = Path(urlparse(item.enclosure_url).path).name or "episode.mp3"
    with tempfile.TemporaryDirectory(prefix="allin-audio-") as tmp:
        dest = podcast.download_enclosure(item.enclosure_url, Path(tmp) / fname)
        _log(f"podcast: downloaded {fname} ({dest.stat().st_size // 1_000_000} MB), transcribing with {model}")
        snippets, info = W.transcribe_file(
            dest, model_name=model, compute_type=compute, beam_size=W.whisper_beam_size(), hotwords=W.whisper_hotwords()
        )
    if not snippets:
        raise RuntimeError("whisper produced no text")
    yt = f"{duration_seconds}s" if duration_seconds is not None else "unknown"
    detail = (
        f"libsyn {fname}; podcast {item.duration_seconds}s vs youtube {yt}; "
        f"{model} {compute} rtf={info['rtf']:.2f}" + (" hotwords" if info.get("hotwords") else "")
    )
    return snippets, f"podcast-whisper:{model}", detail


def fetch_transcript(
    video_id: str,
    settings: Settings,
    *,
    title: str | None = None,
    duration_seconds: int | None = None,
) -> TranscriptResult:
    """Try youtube-transcript-api, yt-dlp captions, then podcast audio + Whisper.

    Raises TranscriptUnavailable (all error messages joined) if every backend fails.
    """
    errors: list[str] = []
    try:
        snippets, gen = _fetch_via_api(video_id, settings)
        if snippets:
            return TranscriptResult(snippets, "youtube-transcript-api", gen)
        errors.append("youtube-transcript-api: empty transcript")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"youtube-transcript-api: {type(exc).__name__}: {str(exc).strip()[:300]}")
    _log(f"{video_id}: transcript API failed, trying yt-dlp fallback")
    try:
        snippets, gen = _fetch_via_ytdlp(video_id, settings)
        if snippets:
            return TranscriptResult(snippets, "yt-dlp", gen)
        errors.append("yt-dlp: empty transcript")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yt-dlp: {type(exc).__name__}: {str(exc).strip()[:300]}")
    _log(f"{video_id}: yt-dlp failed, trying podcast audio + whisper")
    try:
        snippets, source, detail = _fetch_via_podcast(title, duration_seconds, settings)
        return TranscriptResult(snippets, source, True, detail)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"podcast-whisper: {type(exc).__name__}: {str(exc).strip()[:300]}")
    raise TranscriptUnavailable(" | ".join(errors))
