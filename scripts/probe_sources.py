"""Probe transcript/audio sources from wherever this runs (meant for a GitHub Actions runner).

Checks, each reported as {ok, detail}:
  yt_meta_<client>      yt-dlp metadata per player client (duration, caption langs, audio URL present)
  yt_audio_range        ranged GET of the best audio URL (does YouTube's CDN serve the runner?)
  podcast_feed          All-In podcast RSS via iTunes lookup: newest items, durations, transcript tags
  podcast_audio_range   ranged GET of the newest enclosure (non-YouTube audio source)
  whisper_rtf           optional (--whisper): faster-whisper base.en speed on the first 300 s of podcast audio

Usage: python scripts/probe_sources.py [--video 2v8b062QzTI] [--whisper]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def _get(url: str, headers: dict | None = None, rng: str | None = None, timeout: int = 30) -> tuple[int, bytes, dict]:
    h = {"User-Agent": UA, **(headers or {})}
    if rng:
        h["Range"] = rng
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def probe_youtube(video_id: str, report: dict) -> str | None:
    import yt_dlp

    best_url = None
    for client in ("default", "android", "ios", "mweb", "tv_embedded"):
        key = f"yt_meta_{client}"
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        if client != "default":
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            caps = sorted((info.get("automatic_captions") or {}).keys())
            subs = sorted((info.get("subtitles") or {}).keys())
            audio = [f for f in (info.get("formats") or []) if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
            audio.sort(key=lambda f: f.get("abr") or 0, reverse=True)
            url = audio[0].get("url") if audio else None
            if url and best_url is None:
                best_url = url
            report[key] = {
                "ok": bool(info.get("duration")) and ("en" in caps or "en" in subs or bool(url)),
                "detail": f"duration={info.get('duration')} chapters={len(info.get('chapters') or [])} "
                          f"auto_caps_en={'en' in caps} subs_en={'en' in subs} audio_formats={len(audio)}",
            }
        except Exception as e:  # noqa: BLE001
            report[key] = {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:220]}"}
    if best_url:
        try:
            status, body, hdrs = _get(best_url, rng="bytes=0-262143", timeout=40)
            report["yt_audio_range"] = {"ok": status in (200, 206) and len(body) > 100_000,
                                        "detail": f"status={status} bytes={len(body)} type={hdrs.get('Content-Type')}"}
        except Exception as e:  # noqa: BLE001
            report["yt_audio_range"] = {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:220]}"}
    else:
        report["yt_audio_range"] = {"ok": False, "detail": "no audio URL from any client"}
    return best_url


def probe_podcast(report: dict) -> str | None:
    try:
        status, body, _ = _get("https://itunes.apple.com/search?term=all-in%20chamath%20jason%20sacks%20friedberg&entity=podcast&limit=5")
        results = json.loads(body)["results"]
        feed = next((r["feedUrl"] for r in results if "All-In" in r.get("collectionName", "")), None)
        if not feed:
            report["podcast_feed"] = {"ok": False, "detail": "feed not found via iTunes lookup"}
            return None
        status, xml, _ = _get(feed, timeout=40)
        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd", "podcast": "https://podcastindex.org/namespace/1.0"}
        root = ET.fromstring(xml)
        items = root.findall("./channel/item")
        rows = []
        enclosure = None
        for it in items[:4]:
            enc = it.find("enclosure")
            url = enc.get("url") if enc is not None else None
            if url and enclosure is None:
                enclosure = url
            rows.append({
                "title": (it.findtext("title") or "")[:70],
                "pubDate": (it.findtext("pubDate") or "")[:16],
                "duration": it.findtext("itunes:duration", namespaces=ns),
                "transcripts": [x.get("url") for x in it.findall("podcast:transcript", ns)],
            })
        report["podcast_feed"] = {"ok": bool(rows), "detail": f"feed={feed} items={len(items)} newest={json.dumps(rows)[:900]}"}
        if enclosure:
            status, body, hdrs = _get(enclosure, rng="bytes=0-262143", timeout=40)
            report["podcast_audio_range"] = {"ok": status in (200, 206) and len(body) > 100_000,
                                             "detail": f"status={status} bytes={len(body)} type={hdrs.get('Content-Type')} url={enclosure[:120]}"}
        return enclosure
    except Exception as e:  # noqa: BLE001
        report["podcast_feed"] = {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:220]}"}
        return None


def probe_whisper(audio_url: str | None, report: dict) -> None:
    if not audio_url:
        report["whisper_rtf"] = {"ok": False, "detail": "no audio url to test"}
        return
    if not shutil.which("ffmpeg"):
        report["whisper_rtf"] = {"ok": False, "detail": "ffmpeg missing"}
        return
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        report["whisper_rtf"] = {"ok": False, "detail": "faster-whisper not installed (run with: uv run --with faster-whisper ...)"}
        return
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.bin"
        wav = Path(td) / "head.wav"
        status, body, _ = _get(audio_url, rng="bytes=0-8000000", timeout=90)  # ~8 MB covers > 5 min at 128-192 kbps
        src.write_bytes(body)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-t", "300", "-ac", "1", "-ar", "16000", str(wav)], check=False)
        if not wav.exists():
            report["whisper_rtf"] = {"ok": False, "detail": "ffmpeg could not decode the ranged audio"}
            return
        t0 = time.time()
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        load_s = time.time() - t0
        t1 = time.time()
        segments, info = model.transcribe(str(wav), beam_size=1, vad_filter=True)
        segs = list(segments)
        tr_s = time.time() - t1
        audio_s = min(300.0, info.duration or 300.0)
        rtf = tr_s / audio_s if audio_s else None
        sample = " ".join(s.text.strip() for s in segs[:6])[:240]
        report["whisper_rtf"] = {"ok": rtf is not None and rtf < 0.5,
                                 "detail": f"base.en int8 cpu: model_load={load_s:.0f}s transcribe={tr_s:.0f}s for {audio_s:.0f}s audio "
                                           f"-> RTF={rtf:.2f} (90 min episode ~ {rtf*90:.0f} min) segments={len(segs)} sample={sample!r}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="2v8b062QzTI")
    ap.add_argument("--whisper", action="store_true")
    args = ap.parse_args()
    report: dict = {}
    yt_audio = probe_youtube(args.video, report)
    podcast_audio = probe_podcast(report)
    if args.whisper:
        probe_whisper(podcast_audio or yt_audio, report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
