"""Local speech-to-text with faster-whisper (CPU). No ffmpeg needed: PyAV decodes mp3 directly.

Env knobs:
  ALLIN_WHISPER_MODEL    model name (default "small.en")
  ALLIN_WHISPER_COMPUTE  ctranslate2 compute type (default "int8")
  ALLIN_WHISPER_BEAM     beam size (default "1", greedy)
  ALLIN_WHISPER_HOTWORDS comma-separated glossary that faster-whisper puts in front of every decoding
                         window (default: the hosts and the show name, DEFAULT_HOTWORDS); "off" disables it

Why the glossary is on by default (measured on a GitHub-hosted runner, 2026-11-07 episode, small.en int8,
greedy): without it the decoder drifted into lowercase, unpunctuated text with numbers spelled out a few
minutes in and stayed there, and misspelled the hosts; with it the text stays punctuated and the names
come out right, at the same speed. See DECISIONS.md (2026-11-09).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..models import TranscriptSnippet

PROGRESS_EVERY_S = 600.0
# The exact glossary that was measured. Keep it short: every word here is a word the decoder is nudged toward.
DEFAULT_HOTWORDS = (
    "Chamath Palihapitiya, David Sacks, David Friedberg, Jason Calacanis, J-Cal, All-In Podcast, Polymarket"
)
_HOTWORDS_OFF = frozenset({"off", "none", "false", "0"})


class WhisperUnavailable(RuntimeError):
    """faster-whisper is not installed (install the ``whisper`` extra)."""


def whisper_model_name() -> str:
    return os.environ.get("ALLIN_WHISPER_MODEL") or "small.en"


def whisper_compute_type() -> str:
    return os.environ.get("ALLIN_WHISPER_COMPUTE") or "int8"


def whisper_beam_size() -> int:
    return int(os.environ.get("ALLIN_WHISPER_BEAM") or "1")


def whisper_hotwords() -> str | None:
    """The glossary to use: ALLIN_WHISPER_HOTWORDS if set, None when it says "off", else DEFAULT_HOTWORDS."""
    value = (os.environ.get("ALLIN_WHISPER_HOTWORDS") or "").strip()
    if not value:
        return DEFAULT_HOTWORDS
    return None if value.lower() in _HOTWORDS_OFF else value


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _decode_audio(path: Path):
    """Decode to 16 kHz mono float32 with PyAV.

    faster-whisper's own decode_audio passes ``metadata_errors=`` to ``av.open``, which PyAV 18 rejects
    (TypeError), so decoding is done here and the waveform handed to the model.
    """
    import av
    import numpy as np

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(str(path), mode="r") as container:
        frames = container.decode(audio=0)
        while True:
            try:
                frame = next(frames)
            except StopIteration:
                break
            except av.error.InvalidDataError:
                continue
            frame.pts = None
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe_file(
    path: Path,
    *,
    model_name: str,
    compute_type: str = "int8",
    beam_size: int = 1,
    vad_filter: bool = True,
    hotwords: str | None = None,
    log: Callable[[str], None] = _stderr,
) -> tuple[list[TranscriptSnippet], dict]:
    """Transcribe an audio file; returns (snippets, info{model, duration, elapsed_seconds, rtf, hotwords})."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WhisperUnavailable("faster-whisper is not installed (uv sync --extra whisper)") from exc

    t0 = time.time()
    audio = _decode_audio(path)
    model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    options: dict = {"language": "en", "beam_size": beam_size, "vad_filter": vad_filter}
    if hotwords:
        options["hotwords"] = hotwords  # only passed when set, so older faster-whisper builds are unaffected
    segments, tinfo = model.transcribe(audio, **options)
    duration = float(getattr(tinfo, "duration", 0.0) or 0.0)
    snippets: list[TranscriptSnippet] = []
    next_mark = PROGRESS_EVERY_S
    last_end = 0.0
    for seg in segments:  # lazy generator: decoding happens while iterating
        last_end = float(seg.end)
        text = (seg.text or "").strip()
        if text:
            snippets.append(TranscriptSnippet(text=text, start=float(seg.start), duration=max(0.0, float(seg.end) - float(seg.start))))
        if last_end >= next_mark:
            log(f"[whisper] {last_end / 60:.0f}/{duration / 60:.0f} min audio, {time.time() - t0:.0f}s elapsed")
            next_mark += PROGRESS_EVERY_S
    elapsed = time.time() - t0
    audio = duration or last_end
    info = {
        "model": model_name,
        "duration": audio,
        "elapsed_seconds": elapsed,
        "rtf": (elapsed / audio) if audio else 0.0,
        "hotwords": bool(hotwords),
    }
    return snippets, info
