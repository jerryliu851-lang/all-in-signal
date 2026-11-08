"""Runner health checks: can this machine list the playlist, read captions, reach Gamma and the claude CLI?"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any

import httpx

from ..config import Settings


def _log(msg: str) -> None:
    print(f"[verify] {msg}", file=sys.stderr, flush=True)


def _check(fn) -> dict[str, Any]:
    try:
        ok, detail = fn()
        return {"ok": bool(ok), "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}


def verify_runner(settings: Settings) -> dict:
    """Run all checks, print progress to stderr, and raise SystemExit(1) on a hard failure."""
    from .transcripts import _fetch_via_api, _fetch_via_ytdlp
    from .youtube import fetch_video_meta, list_playlist_entries

    results: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {}

    def playlist():
        entries = list_playlist_entries(settings, limit=5)
        state["entries"] = entries
        return bool(entries), f"{len(entries)} entries; in_full_episode_playlist={entries[0].in_full_episode_playlist if entries else None}"

    results["playlist_listing"] = _check(playlist)
    _log(f"playlist_listing: {results['playlist_listing']}")
    entries = state.get("entries") or []
    newest = entries[0].video_id if entries else None

    def meta():
        m = fetch_video_meta(newest, settings)
        return True, f"{m.video_id} {m.published_at} {m.duration_seconds}s chapters={len(m.chapters)}"

    results["newest_episode_meta"] = _check(meta) if newest else {"ok": False, "detail": "no episode"}
    _log(f"newest_episode_meta: {results['newest_episode_meta']}")

    def api():
        t0 = time.time()
        sn, gen = _fetch_via_api(newest, settings)
        return bool(sn), f"{time.time() - t0:.1f}s, {len(sn)} snippets, generated={gen}"

    results["transcript_api"] = _check(api) if newest else {"ok": False, "detail": "no episode"}
    _log(f"transcript_api: {results['transcript_api']}")

    def ytdlp():
        t0 = time.time()
        sn, gen = _fetch_via_ytdlp(newest, settings)
        return bool(sn), f"{time.time() - t0:.1f}s, {len(sn)} snippets, generated={gen}"

    results["transcript_ytdlp_fallback"] = _check(ytdlp) if newest else {"ok": False, "detail": "no episode"}
    _log(f"transcript_ytdlp_fallback: {results['transcript_ytdlp_fallback']}")

    def feed():
        from .podcast import load_feed

        items = load_feed(settings)
        state["feed"] = items
        top = items[0] if items else None
        return bool(items), (
            f"{len(items)} items; newest {top.title[:70]!r} {top.duration_seconds}s" if top else "no items"
        )

    results["podcast_feed"] = _check(feed)
    _log(f"podcast_feed: {results['podcast_feed']}")

    def audio_range():
        item = (state.get("feed") or [None])[0]
        if item is None or not item.enclosure_url:
            return False, "no feed item with an enclosure"
        from .podcast import USER_AGENT

        r = httpx.get(
            item.enclosure_url,
            headers={"Range": "bytes=0-262143", "User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
        return r.status_code in (200, 206), f"HTTP {r.status_code}, {len(r.content)} bytes, {r.headers.get('content-type')}"

    results["podcast_audio_range"] = _check(audio_range)
    _log(f"podcast_audio_range: {results['podcast_audio_range']}")

    def whisper():
        import importlib.util

        from .whisper_transcribe import whisper_model_name

        ok = importlib.util.find_spec("faster_whisper") is not None
        return ok, f"faster_whisper {'importable' if ok else 'NOT installed'}; model={whisper_model_name()}"

    results["whisper_available"] = _check(whisper)

    def rss():
        r = httpx.get(settings.rss_url, timeout=5.0, follow_redirects=True)
        return r.status_code == 200, f"HTTP {r.status_code}"

    results["rss"] = _check(rss)

    def gamma():
        r = httpx.get(settings.gamma_base_url + "/markets?limit=1", timeout=15.0)
        return r.status_code == 200, f"HTTP {r.status_code}"

    results["gamma_api"] = _check(gamma)

    def cli():
        path = shutil.which(settings.claude_bin)
        if not path:
            return False, "claude not on PATH"
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, check=False)
        return out.returncode == 0, (out.stdout.strip() or out.stderr.strip())[:100]

    results["claude_cli"] = _check(cli)
    has_token = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    results["oauth_token_present"] = {"ok": has_token, "detail": str(has_token).lower()}
    deno = shutil.which("deno")
    results["deno_present"] = {"ok": bool(deno), "detail": deno or "not found"}
    results["python_version"] = {"ok": True, "detail": sys.version.split()[0]}

    for name, r in results.items():
        _log(f"{'ok  ' if r['ok'] else 'FAIL'} {name}: {r['detail']}")

    podcast_path = results["podcast_audio_range"]["ok"] and results["whisper_available"]["ok"]
    hard = (not results["playlist_listing"]["ok"]) or not (
        results["transcript_api"]["ok"] or results["transcript_ytdlp_fallback"]["ok"] or podcast_path
    )
    if hard:
        print(__import__("json").dumps(results, indent=2), file=sys.stdout)
        raise SystemExit(1)
    return results
