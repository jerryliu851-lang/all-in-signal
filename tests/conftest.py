from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def load_fixture():
    def _load(rel: str):
        return json.loads((FIXTURES / rel).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def tmp_store(tmp_path):
    from allin.store import JsonFileStore

    return JsonFileStore(tmp_path / "data")


@pytest.fixture
def settings(tmp_path):
    from allin.config import Settings

    return Settings(data_dir=tmp_path / "data", llm_backend="fake")
