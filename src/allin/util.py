from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """UTC timestamp, second precision, RFC 3339 with Z."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def youtube_url(video_id: str, seconds: float | None = None) -> str:
    base = f"https://www.youtube.com/watch?v={video_id}"
    if seconds is None:
        return base
    return f"{base}&t={int(seconds)}s"


def estimate_tokens(text: str, chars_per_token: float = 3.6) -> int:
    """Cheap token estimate when no counting endpoint is available (no API key in this project)."""
    if not text:
        return 0
    return int(len(text) / chars_per_token) + 1


def write_github_output(**kv: Any) -> None:
    """Append key=value pairs to $GITHUB_OUTPUT when running in Actions; no-op elsewhere."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in kv.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, separators=(",", ":"))
            fh.write(f"{k}={v}\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dump_json(obj: Any) -> str:
    """Deterministic JSON for git-friendly diffs."""
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
