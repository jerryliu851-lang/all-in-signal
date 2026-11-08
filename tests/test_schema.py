"""The committed structured-output schema must match the models and be structured-output safe."""

from __future__ import annotations

import jsonschema

from allin.config import ROOT
from allin.models import AnalysisOutput
from allin.schema import analysis_output_schema, load_schema

SCHEMA_PATH = ROOT / "schema" / "episode_analysis.schema.json"


def _walk(node, fn):
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            _walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk(v, fn)


def test_committed_schema_matches_models():
    assert SCHEMA_PATH.exists(), "run `allin schema --write`"
    assert load_schema(SCHEMA_PATH) == analysis_output_schema()


def test_every_object_is_closed_and_fully_required():
    schema = analysis_output_schema()

    def check(node):
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False
            assert sorted(node["required"]) == sorted(node["properties"].keys())

    _walk(schema, check)


def test_no_unsupported_keywords():
    schema = analysis_output_schema()
    banned = {"default", "format", "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "pattern"}

    def check(node):
        assert not (banned & set(node.keys())), f"unsupported keyword in {list(node.keys())}"

    _walk(schema, check)


SAMPLE = {
    "hosts_present": ["Jason Calacanis", "Chamath Palihapitiya"],
    "guests": [],
    "key_takes": [
        {
            "speaker": "Chamath Palihapitiya",
            "attribution_confidence": "high",
            "stance_type": "own_view",
            "summary": "AI capex is front-loaded and will slow.",
            "quote": "the capex cycle is going to slow",
            "timestamp_seconds": 1200,
        }
    ],
    "predictions": [
        {
            "speaker": "David Sacks",
            "attribution_confidence": "medium",
            "stance_type": "own_view",
            "claim": "The Fed cuts rates at the December meeting.",
            "category": "macro",
            "direction": "lower",
            "timeframe": {"text": "December 2026 FOMC", "date": "2026-12-16", "inferred": False},
            "resolution_criteria": "FOMC statement on 2026-12-16 lowers the target range.",
            "stated_confidence": "I think they cut",
            "confidence_bucket": "medium",
            "quote": "I think they cut in December",
            "timestamp_seconds": 2400,
        }
    ],
    "stock_implications": [
        {
            "claim": "Data center spending slows.",
            "tickers": ["NVDA", "SMH"],
            "company": None,
            "is_private": False,
            "sector": "semiconductors",
            "direction": "bearish",
            "timeframe": "next 12 months",
            "rationale": "Slower capex hits accelerator demand.",
            "speaker": "Chamath Palihapitiya",
            "attribution_confidence": "high",
            "timestamp_seconds": 1300,
            "note": None,
        }
    ],
    "macro_themes": [
        {"theme": "ai capex", "stance_summary": "Peak capex is near.", "speakers": ["Chamath Palihapitiya"], "timestamp_seconds": 1200}
    ],
    "polymarket_candidates": [
        {"prediction_index": 0, "question": "Will the Fed cut rates at the December 2026 FOMC meeting?", "hosts_answer": "yes", "implied_probability": 0.65}
    ],
}


def test_sample_validates_against_schema_and_model():
    jsonschema.validate(SAMPLE, analysis_output_schema())
    AnalysisOutput.model_validate(SAMPLE)
