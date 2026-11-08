"""Offline tests for the static site builder (allin.site.build)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from allin.config import Settings
from allin.models import (
    EpisodeAnalysis,
    EpisodeMeta,
    MacroTheme,
    PolymarketState,
    StockImplication,
)
from allin.site.build import PRIVATE_BUCKET, build_site, derive_stocks, derive_themes

SITE_SRC = Settings().site_src_dir


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _analysis(vid: str, date: str, stocks=(), themes=()) -> EpisodeAnalysis:
    return EpisodeAnalysis(
        episode=EpisodeMeta(video_id=vid, title=f"Episode {vid}", published_at=date,
                            url=f"https://www.youtube.com/watch?v={vid}", duration_seconds=5400),
        hosts_present=[], guests=[], key_takes=[], predictions=[],
        stock_implications=list(stocks), macro_themes=list(themes), polymarket_candidates=[],
        analyzed_at="2026-11-07T00:00:00Z", model="test", backend="fake",
    )


def _stock(vid: str, n: int, tickers: list[str], direction: str, speaker="David Sacks", ts=100, **kw):
    return StockImplication(
        id=f"{vid}-s{n:02d}", claim=f"claim {n}", tickers=tickers, company=kw.get("company"),
        is_private=kw.get("is_private", False), sector=kw.get("sector"), direction=direction,
        timeframe="12 months", rationale=f"why {n}", speaker=speaker, attribution_confidence="high",
        timestamp_seconds=ts, note=kw.get("note"),
    )


@pytest.fixture
def sample_out(tmp_path, settings) -> Path:
    return build_site(None, settings, tmp_path / "_site", sample_data=True)


def test_sample_build_writes_expected_files(sample_out, settings):
    for rel in ("index.html", "css/app.css", "js/app.js", "data/manifest.json", "data/latest.json",
                "data/stocks.json", "data/themes.json", "data/predictions.json", "data/scorecard.json",
                "data/polymarket.json", "data/index.json", "data/runs.json"):
        assert (sample_out / rel).is_file(), rel
    episodes = sorted((sample_out / "data" / "episodes").glob("*.json"))
    assert len(episodes) == 3

    manifest = _read(sample_out / "data" / "manifest.json")
    assert manifest["synthetic"] is True
    assert manifest["episode_count"] == 3
    ledger = _read(sample_out / "data" / "predictions.json")
    assert manifest["prediction_count"] == ledger["count"] == len(ledger["predictions"])
    assert 20 <= ledger["count"] <= 35
    for rel in manifest["files"]:
        assert (sample_out / rel).is_file(), rel
    # synthetic data never lands in the real data dir
    assert not list(settings.data_dir.rglob("*.json"))


def test_sample_data_covers_every_bucket(sample_out):
    preds = _read(sample_out / "data" / "predictions.json")["predictions"]
    assert {p["status"] for p in preds} == {"pending", "resolved_correct", "resolved_incorrect", "unresolvable"}
    assert {p["category"] for p in preds} == {"macro", "markets", "politics", "tech", "AI", "crypto",
                                               "company-specific", "other"}
    assert {p["confidence_bucket"] for p in preds} == {"high", "medium", "low", "unstated"}
    assert {p["stance_type"] for p in preds} == {"own_view", "reported_view", "devils_advocate"}
    assert any(p["timeframe_passed"] for p in preds)
    hosts = set(Settings().hosts)
    assert hosts <= {p["speaker"] for p in preds}

    pm = _read(sample_out / "data" / "polymarket.json")
    assert any(m["divergence_points"] > 25 and m["flagged_divergence"] for m in pm["matches"])
    assert any(m["closed"] and m["resolution_outcome"] for m in pm["matches"])
    assert pm["unmatched"]

    stocks = _read(sample_out / "data" / "stocks.json")
    tickers = {t["ticker"] for t in stocks["tickers"]}
    assert {"NVDA", "TSLA", "SMH", "TLT"} <= tickers
    assert any(c["is_private"] for c in stocks[PRIVATE_BUCKET])

    themes = _read(sample_out / "data" / "themes.json")["themes"]
    assert themes[0]["episode_count"] >= 2

    latest = _read(sample_out / "data" / "latest.json")
    vid = latest["analysis"]["episode"]["video_id"]
    assert vid == _read(sample_out / "data" / "manifest.json")["latest_episode_id"]
    assert latest["ledger"] and all(e["episode_id"] == vid for e in latest["ledger"])
    assert all(m["episode_id"] == vid for m in latest["polymarket_matches"])

    sc = _read(sample_out / "data" / "scorecard.json")
    assert set(sc["hosts"]) == hosts
    assert sc["others"]


def test_sample_build_is_deterministic(tmp_path, settings):
    a = build_site(None, settings, tmp_path / "a", sample_data=True)
    b = build_site(None, settings, tmp_path / "b", sample_data=True)
    for rel in ("predictions.json", "stocks.json", "themes.json", "latest.json", "polymarket.json"):
        assert (a / "data" / rel).read_text() == (b / "data" / rel).read_text(), rel


def test_stocks_grouping_and_net_score():
    e1 = _analysis("v1", "2026-10-01", stocks=[
        _stock("v1", 1, ["NVDA"], "bullish", ts=50),
        _stock("v1", 2, ["nvda", "SMH"], "bullish", ts=60),
        _stock("v1", 3, [], "bullish", company="SpaceX", is_private=True, note="private"),
    ])
    e2 = _analysis("v2", "2026-10-08", stocks=[
        _stock("v2", 1, ["NVDA"], "bearish", ts=10),
        _stock("v2", 2, ["SMH"], "neutral", ts=20),
    ])
    e3 = _analysis("v3", "2026-10-15", stocks=[_stock("v3", 1, ["NVDA"], "bearish", ts=99)])
    # input order should not matter: derive_stocks sorts chronologically
    doc = derive_stocks([e3, e1, e2])
    by = {t["ticker"]: t for t in doc["tickers"]}
    nvda = by["NVDA"]
    assert nvda["call_count"] == 4
    assert nvda["net_score"] == 0  # +1 +1 -1 -1
    assert [p["cumulative"] for p in nvda["series"]] == [2, 1, 0]
    assert [p["delta"] for p in nvda["series"]] == [2, -1, -1]
    assert (nvda["bullish"], nvda["bearish"], nvda["neutral"]) == (2, 2, 0)
    assert nvda["calls"][0]["timestamp_url"] == "https://www.youtube.com/watch?v=v1&t=50s"
    assert nvda["calls"][0]["episode_title"] == "Episode v1"
    smh = by["SMH"]
    assert smh["net_score"] == 1 and [p["cumulative"] for p in smh["series"]] == [1, 1]
    assert doc["tickers"][0]["ticker"] == "NVDA"  # most calls first
    priv = doc[PRIVATE_BUCKET]
    assert len(priv) == 1 and priv[0]["company"] == "SpaceX" and priv[0]["is_private"] is True
    for key in ("episode_id", "episode_title", "published_at", "speaker", "direction", "timeframe",
                "rationale", "claim", "timestamp_url"):
        assert key in nvda["calls"][0]


def test_themes_aggregation():
    e1 = _analysis("v1", "2026-10-01", themes=[
        MacroTheme(theme="Rates", stance_summary="hold", speakers=["David Sacks"], timestamp_seconds=10),
        MacroTheme(theme="AI capex", stance_summary="bullish", speakers=[], timestamp_seconds=20),
    ])
    e2 = _analysis("v2", "2026-10-08", themes=[
        MacroTheme(theme=" rates ", stance_summary="cut", speakers=["Chamath Palihapitiya"], timestamp_seconds=30),
    ])
    themes = derive_themes([e2, e1])["themes"]
    assert [t["theme"] for t in themes] == ["rates", "ai capex"]
    rates = themes[0]
    assert rates["frequency"] == 2 and rates["episode_count"] == 2
    assert (rates["first_seen"], rates["last_seen"]) == ("2026-10-01", "2026-10-08")
    assert [e["stance_summary"] for e in rates["entries"]] == ["hold", "cut"]
    assert rates["entries"][1]["speakers"] == ["Chamath Palihapitiya"]
    assert rates["entries"][0]["timestamp_url"].endswith("&t=10s")


def test_build_from_empty_store(tmp_path, tmp_store, settings):
    out = build_site(tmp_store, settings, tmp_path / "_site", sample_data=False)
    data = out / "data"
    manifest = _read(data / "manifest.json")
    assert manifest["synthetic"] is False
    assert manifest["episode_count"] == 0 and manifest["prediction_count"] == 0
    assert manifest["latest_episode_id"] is None
    assert _read(data / "predictions.json")["predictions"] == []
    assert _read(data / "scorecard.json")["hosts"] == {}
    assert PolymarketState.model_validate(_read(data / "polymarket.json")).matches == []
    assert _read(data / "runs.json") == []
    assert _read(data / "index.json")["episodes"] == {}
    assert _read(data / "latest.json")["analysis"] is None
    assert _read(data / "stocks.json") == {"tickers": [], PRIVATE_BUCKET: []}
    assert _read(data / "themes.json") == {"themes": []}
    assert (data / "episodes").is_dir()
    assert (out / "index.html").is_file()


def test_build_from_store_with_analysis_derives_ledger(tmp_path, tmp_store, settings):
    tmp_store.save_analysis(_analysis("v1", "2026-10-01", stocks=[_stock("v1", 1, ["TLT"], "bearish")]))
    out = build_site(tmp_store, settings, tmp_path / "_site")
    assert (out / "data" / "episodes" / "v1.json").is_file()
    assert _read(out / "data" / "stocks.json")["tickers"][0]["ticker"] == "TLT"
    assert _read(out / "data" / "latest.json")["analysis"]["episode"]["video_id"] == "v1"


def test_rebuild_cleans_stale_files(tmp_path, settings):
    out = tmp_path / "_site"
    build_site(None, settings, out, sample_data=True)
    (out / "stale.txt").write_text("old")
    build_site(None, settings, out, sample_data=True)
    assert not (out / "stale.txt").exists()


def test_refuses_unsafe_out_dir(settings):
    with pytest.raises(ValueError):
        build_site(None, settings, settings.root, sample_data=True)
    with pytest.raises(ValueError):
        build_site(None, settings, Path("/"), sample_data=True)
    with pytest.raises(ValueError):
        build_site(None, settings, settings.site_src_dir / "out", sample_data=True)


def test_no_absolute_fetch_paths():
    for js in (SITE_SRC / "js").rglob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "fetch('/" not in text and 'fetch("/' not in text and "fetch(`/" not in text, js


def test_index_html_references_existing_files():
    html = (SITE_SRC / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="([^"]+)"', html)
    local = [r for r in refs if not r.startswith(("http://", "https://", "#", "data:", "mailto:"))]
    assert local, "expected local css/js references"
    for ref in local:
        assert not ref.startswith("/"), f"absolute path breaks GitHub Pages sub-path: {ref}"
        assert (SITE_SRC / ref).is_file(), ref


def test_js_module_imports_resolve():
    for js in (SITE_SRC / "js").rglob("*.js"):
        for spec in re.findall(r'^\s*import[^"\']*["\']([^"\']+)["\']', js.read_text(encoding="utf-8"), re.MULTILINE):
            assert spec.startswith("."), f"{js}: bare import {spec}"
            assert (js.parent / spec).resolve().is_file(), f"{js}: {spec}"


def test_external_resources_allowed_only():
    html = (SITE_SRC / "index.html").read_text(encoding="utf-8")
    for url in re.findall(r'(?:href|src)="(https?://[^"]+)"', html):
        assert url.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com")), url
