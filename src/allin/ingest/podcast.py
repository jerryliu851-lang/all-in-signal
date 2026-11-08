"""Podcast-feed helpers: episode metadata and audio when YouTube blocks the runner.

The show's libsyn feed is reachable from GitHub-hosted runners and its audio cut is identical to the
YouTube cut (durations agree to the second), so feed audio transcribed locally yields timestamps that
line up with the YouTube video.

Env: ALLIN_PODCAST_FEED = feed URL (default: the libsyn feed below) or "auto" (iTunes lookup).
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from pydantic import BaseModel
from rapidfuzz import fuzz

from ..config import Settings
from ..models import Chapter
from ..util import youtube_url
from .youtube import PlaylistEntry, VideoMeta

DEFAULT_FEED_URL = "https://rss.libsyn.com/shows/254861/destinations/1928300.xml"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/search?term=all-in%20chamath%20jason%20sacks%20friedberg&entity=podcast"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
DURATION_TOLERANCE_S = 5
TITLE_ONLY_MIN_SCORE = 90

_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
_FEED_CACHE: dict[str, list[FeedItem]] = {}


def _log(msg: str) -> None:
    print(f"[ingest] {msg}", file=sys.stderr, flush=True)


class FeedItem(BaseModel):
    title: str
    pub_date: str | None = None  # YYYY-MM-DD
    duration_seconds: int | None = None
    enclosure_url: str = ""
    description: str = ""
    guid: str = ""


def parse_duration(text: str | None) -> int | None:
    """itunes:duration as HH:MM:SS, MM:SS or plain seconds -> int seconds (None if unparseable)."""
    if not text:
        return None
    s = text.strip()
    try:
        if ":" in s:
            parts = [int(float(p)) for p in s.split(":")]
            if len(parts) > 3:
                return None
            total = 0
            for p in parts:
                total = total * 60 + p
            return total
        return int(float(s))
    except ValueError:
        return None


def _parse_pub_date(text: str | None) -> str | None:
    if not text:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(text.strip()).date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_feed(xml_bytes: bytes) -> list[FeedItem]:
    root = ET.fromstring(xml_bytes)
    items: list[FeedItem] = []
    for it in root.iter("item"):
        enc = it.find("enclosure")
        desc = it.findtext("description") or it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or ""
        items.append(
            FeedItem(
                title=(it.findtext("title") or "").strip(),
                pub_date=_parse_pub_date(it.findtext("pubDate")),
                duration_seconds=parse_duration(it.findtext(f"{_ITUNES_NS}duration")),
                enclosure_url=(enc.get("url") or "") if enc is not None else "",
                description=desc,
                guid=(it.findtext("guid") or "").strip(),
            )
        )
    return items


def _resolve_feed_url(client: httpx.Client) -> str:
    configured = os.environ.get("ALLIN_PODCAST_FEED") or DEFAULT_FEED_URL
    if configured.strip().lower() != "auto":
        return configured
    r = client.get(ITUNES_LOOKUP_URL)
    r.raise_for_status()
    for res in r.json().get("results") or []:
        if res.get("feedUrl"):
            return res["feedUrl"]
    raise RuntimeError("iTunes lookup returned no feedUrl")


def load_feed(settings: Settings, *, client: httpx.Client | None = None) -> list[FeedItem]:
    """Fetch and parse the podcast feed (cached in-process per feed URL)."""
    own = client is None
    c = client or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}, proxy=None)
    try:
        url = _resolve_feed_url(c)
        if url in _FEED_CACHE:
            return _FEED_CACHE[url]
        r = c.get(url)
        r.raise_for_status()
        items = parse_feed(r.content)
        _FEED_CACHE[url] = items
        _log(f"podcast feed: {len(items)} items")
        return items
    finally:
        if own:
            c.close()


def match_feed_item(items: list[FeedItem], *, title: str, duration_seconds: int | None) -> FeedItem | None:
    """Duration within 5 s (best title score breaks ties); else a title-only match scoring >= 90."""

    def score(it: FeedItem) -> float:
        return fuzz.token_set_ratio(title, it.title)

    if duration_seconds is not None:
        close = [
            it for it in items
            if it.duration_seconds is not None and abs(it.duration_seconds - duration_seconds) <= DURATION_TOLERANCE_S
        ]
        if close:
            return max(close, key=score)
    if title:
        best = max(items, key=score, default=None)
        if best is not None and score(best) >= TITLE_ONLY_MIN_SCORE:
            return best
    return None


def download_enclosure(url: str, dest: Path, *, client: httpx.Client | None = None) -> Path:
    """Stream the enclosure to ``dest``."""
    own = client is None
    c = client or httpx.Client(
        timeout=httpx.Timeout(120.0), follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with c.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
    finally:
        if own:
            c.close()
    return dest


_CHAPTER = re.compile(r"^\s*[(\[]?\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*[)\]]?\s*[-–—:]?\s*(.+?)\s*$")


def parse_chapters_from_description(text: str) -> list[Chapter]:
    """Lines like '(0:00) Intro', '(1:05:12) Science Corner' or '12:34 Title'; [] when none."""
    plain = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text or "", flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    found: list[tuple[float, str]] = []
    for line in plain.splitlines():
        m = _CHAPTER.match(line)
        if not m:
            continue
        secs = parse_duration(m.group(1))
        if secs is None:
            continue
        found.append((float(secs), m.group(2)))
    if not found:
        return []
    chapters: list[Chapter] = []
    for i, (start, title) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else None
        chapters.append(Chapter(start_time=start, end_time=end, title=title))
    return chapters


def meta_from_feed_item(entry: PlaylistEntry, item: FeedItem) -> VideoMeta:
    """Build VideoMeta from a flat playlist entry plus the matched feed item."""
    return VideoMeta(
        video_id=entry.video_id,
        title=entry.title or item.title,
        published_at=item.pub_date,
        duration_seconds=item.duration_seconds if item.duration_seconds is not None else entry.duration_seconds,
        chapters=parse_chapters_from_description(item.description),
        url=entry.url or youtube_url(entry.video_id),
        live_status=entry.live_status,
    )
