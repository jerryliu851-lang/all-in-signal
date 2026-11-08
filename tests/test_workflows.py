from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
FILES = sorted(WF.glob("*.yml"))


def load(name):
    return yaml.safe_load((WF / name).read_text())


def triggers(wf):
    return wf.get("on", wf.get(True))


def test_all_parse():
    assert {f.name for f in FILES} >= {"pipeline.yml", "backfill.yml", "verify-runner.yml", "ci.yml"}
    for f in FILES:
        assert isinstance(yaml.safe_load(f.read_text()), dict)


def test_concurrency_shared():
    for n in ("pipeline.yml", "backfill.yml"):
        c = load(n)["concurrency"]
        assert c["group"] == "all-in-signal-pipeline"
        assert c["cancel-in-progress"] is False


def test_pipeline_cron_and_analyze_condition():
    wf = load("pipeline.yml")
    assert triggers(wf)["schedule"][0]["cron"] == "17 */6 * * *"
    steps = wf["jobs"]["run"]["steps"]
    analyze = next(s for s in steps if s.get("run") == "uv run allin analyze")
    assert analyze["if"] == "steps.ingest.outputs.pending_analysis != '0'"


def test_token_secret_present():
    for n in ("pipeline.yml", "backfill.yml", "verify-runner.yml"):
        assert "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in (WF / n).read_text()


def test_no_api_key_anywhere():
    for f in list(WF.glob("*")) + [ROOT / "README.md"]:
        assert "ANTHROPIC_API_KEY" not in f.read_text()


def test_permissions():
    p = load("pipeline.yml")["permissions"]
    assert p["pages"] == "write" and p["id-token"] == "write" and p["contents"] == "write"


def test_readme_sections():
    r = (ROOT / "README.md").read_text()
    for h in ("Future: trading-bot integration", "Future: price-based scoring", "Disclaimer",
              "Known limitations", "Storage decision", "<!-- COST_TABLE_START -->", "<!-- COST_TABLE_END -->"):
        assert h in r


def test_tee_steps_use_pipefail():
    """Red-team regression: GitHub's default shell is `bash -e` without pipefail, so
    `allin ... | tee file` reported success even when allin exited 1."""
    for f in FILES:
        for job in yaml.safe_load(f.read_text())["jobs"].values():
            for step in job.get("steps", []):
                run = step.get("run") or ""
                if "| tee" in run and step.get("shell") != "bash":
                    assert "set -o pipefail" in run, f"{f.name}: {step.get('name')}"


def test_backfill_rejects_zero_count():
    steps = load("backfill.yml")["jobs"]["backfill"]["steps"]
    validate = next(s for s in steps if s.get("name") == "Validate count")
    assert '-lt 1' in validate["run"]
