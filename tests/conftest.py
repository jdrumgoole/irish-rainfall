"""Shared fixtures for the irish-rainfall test suite.

Tests run against a copy of the production rainfall.db staged at
tests/data/rainfall.db by `invoke prepare-test-data`. The file is gitignored.
SQLite reads are concurrency-safe so the same on-disk DB is shared across
parallel test workers without contention.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

TEST_DB_PATH = Path(__file__).parent / "data" / "rainfall.db"


@pytest.fixture(scope="session")
def test_db_path() -> Path:
    if not TEST_DB_PATH.exists():
        pytest.skip(
            f"Test database missing at {TEST_DB_PATH}. "
            "Run `uv run invoke prepare-test-data`."
        )
    return TEST_DB_PATH


@pytest.fixture
def db(test_db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(test_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(test_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from irish_rainfall import app as app_module

    monkeypatch.setattr(app_module, "DEFAULT_DB_PATH", test_db_path)
    return TestClient(app_module.app)
