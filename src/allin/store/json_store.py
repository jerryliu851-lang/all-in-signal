"""Committed-JSON implementation of ``Store``.

Layout under ``data/``::

    index.json                          EpisodeIndex
    transcripts/<id>.raw.json           {"source": ..., "snippets": [TranscriptSnippet...]}
    transcripts/<id>.normalized.json    NormalizedTranscript
    episodes/<id>.json                  EpisodeAnalysis
    polymarket.json                     PolymarketState
    predictions.json                    PredictionsLedger   (consumer-facing, stable schema)
    scorecard.json                      Scorecard
    runs.json                           [RunRecord...]      (most recent last)

Writes are atomic (temp file + rename) and deterministic (2-space indent, UTF-8) for clean diffs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from ..models import (
    EpisodeAnalysis,
    EpisodeIndex,
    NormalizedTranscript,
    PolymarketState,
    PredictionsLedger,
    RunRecord,
    Scorecard,
    TranscriptSnippet,
)
from ..util import atomic_write_text, dump_json

M = TypeVar("M", bound=BaseModel)


class JsonFileStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.transcripts_dir = self.data_dir / "transcripts"
        self.episodes_dir = self.data_dir / "episodes"
        for d in (self.data_dir, self.transcripts_dir, self.episodes_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- helpers -------------------------------------------------------------------
    def _read(self, path: Path) -> Any | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, path: Path, obj: Any) -> None:
        if isinstance(obj, BaseModel):
            obj = obj.model_dump(mode="json")
        atomic_write_text(path, dump_json(obj))

    def _read_model(self, path: Path, model: type[M]) -> M | None:
        raw = self._read(path)
        return model.model_validate(raw) if raw is not None else None

    # ---- paths ---------------------------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.json"

    @property
    def polymarket_path(self) -> Path:
        return self.data_dir / "polymarket.json"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "predictions.json"

    @property
    def scorecard_path(self) -> Path:
        return self.data_dir / "scorecard.json"

    @property
    def runs_path(self) -> Path:
        return self.data_dir / "runs.json"

    def analysis_path(self, video_id: str) -> Path:
        return self.episodes_dir / f"{video_id}.json"

    def raw_transcript_path(self, video_id: str) -> Path:
        return self.transcripts_dir / f"{video_id}.raw.json"

    def normalized_transcript_path(self, video_id: str) -> Path:
        return self.transcripts_dir / f"{video_id}.normalized.json"

    # ---- index ---------------------------------------------------------------------
    def load_index(self) -> EpisodeIndex:
        return self._read_model(self.index_path, EpisodeIndex) or EpisodeIndex()

    def save_index(self, index: EpisodeIndex) -> None:
        self._write(self.index_path, index)

    # ---- transcripts ---------------------------------------------------------------
    def save_raw_transcript(self, video_id: str, snippets: list[TranscriptSnippet], source: str) -> None:
        self._write(
            self.raw_transcript_path(video_id),
            {"video_id": video_id, "source": source, "snippets": [s.model_dump(mode="json") for s in snippets]},
        )

    def load_raw_transcript(self, video_id: str) -> list[TranscriptSnippet] | None:
        raw = self._read(self.raw_transcript_path(video_id))
        if raw is None:
            return None
        return [TranscriptSnippet.model_validate(s) for s in raw.get("snippets", [])]

    def save_normalized_transcript(self, transcript: NormalizedTranscript) -> None:
        self._write(self.normalized_transcript_path(transcript.video_id), transcript)

    def load_normalized_transcript(self, video_id: str) -> NormalizedTranscript | None:
        return self._read_model(self.normalized_transcript_path(video_id), NormalizedTranscript)

    # ---- analyses ------------------------------------------------------------------
    def save_analysis(self, analysis: EpisodeAnalysis) -> None:
        self._write(self.analysis_path(analysis.episode.video_id), analysis)

    def load_analysis(self, video_id: str) -> EpisodeAnalysis | None:
        return self._read_model(self.analysis_path(video_id), EpisodeAnalysis)

    def list_analyses(self) -> list[EpisodeAnalysis]:
        out: list[EpisodeAnalysis] = []
        for p in sorted(self.episodes_dir.glob("*.json")):
            a = self._read_model(p, EpisodeAnalysis)
            if a is not None:
                out.append(a)
        out.sort(key=lambda a: (a.episode.published_at, a.episode.video_id), reverse=True)
        return out

    # ---- polymarket ----------------------------------------------------------------
    def load_polymarket(self) -> PolymarketState:
        return self._read_model(self.polymarket_path, PolymarketState) or PolymarketState()

    def save_polymarket(self, state: PolymarketState) -> None:
        self._write(self.polymarket_path, state)

    # ---- derived -------------------------------------------------------------------
    def save_ledger(self, ledger: PredictionsLedger) -> None:
        self._write(self.ledger_path, ledger)

    def load_ledger(self) -> PredictionsLedger | None:
        return self._read_model(self.ledger_path, PredictionsLedger)

    def save_scorecard(self, scorecard: Scorecard) -> None:
        self._write(self.scorecard_path, scorecard)

    def load_scorecard(self) -> Scorecard | None:
        return self._read_model(self.scorecard_path, Scorecard)

    # ---- runs ----------------------------------------------------------------------
    def append_run(self, record: RunRecord, keep_last: int = 200) -> None:
        runs = self.list_runs()
        runs.append(record)
        runs = runs[-keep_last:]
        self._write(self.runs_path, [r.model_dump(mode="json") for r in runs])

    def list_runs(self) -> list[RunRecord]:
        raw = self._read(self.runs_path) or []
        return [RunRecord.model_validate(r) for r in raw]
