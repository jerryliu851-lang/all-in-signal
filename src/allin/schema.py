"""JSON Schema for the LLM's structured output, generated from the pydantic models.

The committed copy lives at schema/episode_analysis.schema.json; ``allin schema --check`` fails
if it drifts from the models. The schema is post-processed into the subset structured outputs
accept: every object forbids extra keys, every property is required, and pydantic-only keys
(defaults, formats, numeric bounds) are stripped so the model is steered by descriptions, not
by validator keywords it may not support.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AnalysisOutput

_STRIP_KEYS = {"default", "format", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
               "minItems", "maxItems", "minLength", "maxLength", "pattern", "uniqueItems"}


def _tighten(node: Any) -> Any:
    if isinstance(node, list):
        return [_tighten(n) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _STRIP_KEYS:
            continue
        out[k] = _tighten(v)
    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"].keys())
    return out


def analysis_output_schema() -> dict[str, Any]:
    raw = AnalysisOutput.model_json_schema()
    schema = _tighten(raw)
    # No "$schema" key: the Claude Code CLI's validator rejects the 2020-12 meta-schema URI
    # ("--json-schema is not a valid JSON Schema: no schema with key or ref ...").
    schema["title"] = "AllInSignalEpisodeAnalysis"
    return schema


def write_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis_output_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_matches_models(path: Path) -> bool:
    if not path.exists():
        return False
    return load_schema(path) == analysis_output_schema()
