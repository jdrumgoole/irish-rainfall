"""Regression tests for the cross-region rainfall API endpoints.

For each endpoint, the expected aggregate is computed independently against
the same SQLite database using the simplest possible SQL — deliberately
different from the SQL the endpoint uses, so that any regression introduced
by query optimisation will surface as a mismatch.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Float comparison tolerance for aggregated rainfall in millimetres.
MM_TOL = 1e-6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approx(value: float | None) -> Any:
    return pytest.approx(value, abs=MM_TOL) if value is not None else None


def _annual_totals(
    db: sqlite3.Connection, start_year: int, end_year: int
) -> dict[tuple[int, int], float]:
    """Return {(station_id, year): total_mm} for the year range."""
    rows = db.execute(
        """
        SELECT station_id, year, SUM(amount_mm) AS total
        FROM rainfall
        WHERE year BETWEEN ? AND ?
        GROUP BY station_id, year
        """,
        (start_year, end_year),
    ).fetchall()
    return {(r["station_id"], r["year"]): r["total"] for r in rows}


def _station_id_by_name(db: sqlite3.Connection) -> dict[str, int]:
    return {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM stations")}


# ---------------------------------------------------------------------------
# /api/stations
# ---------------------------------------------------------------------------

def test_stations_count_matches_db(client: TestClient, db: sqlite3.Connection) -> None:
    expected = db.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    response = client.get("/api/stations")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == expected
    assert {"id", "name", "county", "latitude", "longitude"} <= payload[0].keys()


def test_stations_sorted_by_name(client: TestClient) -> None:
    names = [s["name"] for s in client.get("/api/stations").json()]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# /api/rainfall/annual
# ---------------------------------------------------------------------------

def test_annual_per_station_matches_independent_sum(
    client: TestClient, db: sqlite3.Connection
) -> None:
    station_id = db.execute("SELECT id FROM stations ORDER BY id LIMIT 1").fetchone()[0]
    response = client.get(
        "/api/rainfall/annual",
        params={"station_id": station_id, "start_year": 1900, "end_year": 1910},
    )
    assert response.status_code == 200
    api = {r["year"]: r["annual_total"] for r in response.json()}
    expected = {
        y: t
        for (sid, y), t in _annual_totals(db, 1900, 1910).items()
        if sid == station_id
    }
    assert api.keys() == expected.keys()
    for year, value in expected.items():
        assert api[year] == _approx(value)


def test_annual_national_average_is_mean_of_station_totals(
    client: TestClient, db: sqlite3.Connection
) -> None:
    response = client.get(
        "/api/rainfall/annual", params={"start_year": 1950, "end_year": 1955}
    )
    assert response.status_code == 200
    api = {r["year"]: r["annual_total"] for r in response.json()}

    by_year: dict[int, list[float]] = {}
    for (_, year), total in _annual_totals(db, 1950, 1955).items():
        by_year.setdefault(year, []).append(total)
    expected = {y: sum(v) / len(v) for y, v in by_year.items()}

    assert api.keys() == expected.keys()
    for year, value in expected.items():
        assert api[year] == _approx(value)


# ---------------------------------------------------------------------------
# /api/rainfall/seasonal
# ---------------------------------------------------------------------------

SEASON_MONTHS = {
    "Winter": (12, 1, 2),
    "Spring": (3, 4, 5),
    "Summer": (6, 7, 8),
    "Autumn": (9, 10, 11),
}


def test_seasonal_average_matches_independent_calculation(
    client: TestClient, db: sqlite3.Connection
) -> None:
    response = client.get(
        "/api/rainfall/seasonal", params={"start_year": 1960, "end_year": 1969}
    )
    assert response.status_code == 200
    api = {(r["station_name"], r["season"]): r["avg_monthly_rainfall"] for r in response.json()}

    name_by_id = {sid: name for name, sid in _station_id_by_name(db).items()}
    expected: dict[tuple[str, str], float] = {}
    for season, months in SEASON_MONTHS.items():
        placeholders = ",".join("?" * len(months))
        rows = db.execute(
            f"""
            SELECT station_id, AVG(amount_mm) AS avg_mm
            FROM rainfall
            WHERE year BETWEEN ? AND ? AND month IN ({placeholders})
            GROUP BY station_id
            """,
            (1960, 1969, *months),
        ).fetchall()
        for r in rows:
            expected[(name_by_id[r["station_id"]], season)] = r["avg_mm"]

    assert api.keys() == expected.keys()
    for key, value in expected.items():
        assert api[key] == _approx(value)


def test_seasonal_returns_four_seasons_per_station(
    client: TestClient, db: sqlite3.Connection
) -> None:
    rows = client.get("/api/rainfall/seasonal").json()
    by_station: dict[str, set[str]] = {}
    for row in rows:
        by_station.setdefault(row["station_name"], set()).add(row["season"])
    for station, seasons in by_station.items():
        assert seasons == {"Winter", "Spring", "Summer", "Autumn"}, station


# ---------------------------------------------------------------------------
# /api/rainfall/climatology
# ---------------------------------------------------------------------------

def test_climatology_matches_independent_monthly_average(
    client: TestClient, db: sqlite3.Connection
) -> None:
    response = client.get(
        "/api/rainfall/climatology", params={"start_year": 1970, "end_year": 1979}
    )
    assert response.status_code == 200
    api = {(r["station_name"], r["month"]): r["avg_rainfall"] for r in response.json()}

    name_by_id = {sid: name for name, sid in _station_id_by_name(db).items()}
    rows = db.execute(
        """
        SELECT station_id, month, AVG(amount_mm) AS avg_mm
        FROM rainfall
        WHERE year BETWEEN ? AND ?
        GROUP BY station_id, month
        """,
        (1970, 1979),
    ).fetchall()
    expected = {(name_by_id[r["station_id"]], r["month"]): r["avg_mm"] for r in rows}

    assert api.keys() == expected.keys()
    for key, value in expected.items():
        assert api[key] == _approx(value)


def test_climatology_covers_all_twelve_months(client: TestClient) -> None:
    rows = client.get("/api/rainfall/climatology").json()
    by_station: dict[str, set[int]] = {}
    for r in rows:
        by_station.setdefault(r["station_name"], set()).add(r["month"])
    for station, months in by_station.items():
        assert months == set(range(1, 13)), station


# ---------------------------------------------------------------------------
# /api/rainfall/station-summary
# ---------------------------------------------------------------------------

def test_station_summary_aggregates_match_annual_totals(
    client: TestClient, db: sqlite3.Connection
) -> None:
    response = client.get("/api/rainfall/station-summary")
    assert response.status_code == 200
    api = {r["name"]: r for r in response.json()}

    name_by_id = {sid: name for name, sid in _station_id_by_name(db).items()}
    by_station: dict[str, list[float]] = {}
    for (sid, _), total in _annual_totals(db, 1, 9999).items():
        by_station.setdefault(name_by_id[sid], []).append(total)

    assert set(api.keys()) == set(by_station.keys())
    for name, totals in by_station.items():
        row = api[name]
        assert row["avg_annual_rainfall"] == _approx(sum(totals) / len(totals))
        assert row["min_annual_rainfall"] == _approx(min(totals))
        assert row["max_annual_rainfall"] == _approx(max(totals))
        assert row["years_of_data"] == len(totals)


def test_station_summary_sorted_by_avg_descending(client: TestClient) -> None:
    rows = client.get("/api/rainfall/station-summary").json()
    averages = [r["avg_annual_rainfall"] for r in rows]
    assert averages == sorted(averages, reverse=True)


# ---------------------------------------------------------------------------
# /api/rainfall/comparison
# ---------------------------------------------------------------------------

def test_comparison_period_averages_match_independent_calculation(
    client: TestClient, db: sqlite3.Connection
) -> None:
    p1 = (1850, 1900)
    p2 = (1960, 2010)
    response = client.get(
        "/api/rainfall/comparison",
        params={
            "period1_start": p1[0],
            "period1_end": p1[1],
            "period2_start": p2[0],
            "period2_end": p2[1],
        },
    )
    assert response.status_code == 200
    api = {r["station_name"]: r for r in response.json()}

    name_by_id = {sid: name for name, sid in _station_id_by_name(db).items()}

    def avg_per_station(start: int, end: int) -> dict[str, float]:
        annual = _annual_totals(db, start, end)
        bucket: dict[int, list[float]] = {}
        for (sid, _), total in annual.items():
            bucket.setdefault(sid, []).append(total)
        return {name_by_id[sid]: sum(v) / len(v) for sid, v in bucket.items()}

    p1_avg = avg_per_station(*p1)
    p2_avg = avg_per_station(*p2)
    expected_names = set(p1_avg.keys()) & set(p2_avg.keys())
    assert set(api.keys()) == expected_names

    for name in expected_names:
        row = api[name]
        assert row["period1_avg"] == _approx(p1_avg[name])
        assert row["period2_avg"] == _approx(p2_avg[name])
        change = p2_avg[name] - p1_avg[name]
        assert row["change"] == _approx(change)
        assert row["change_percent"] == _approx(change / p1_avg[name] * 100)


def test_comparison_sorted_by_change_percent_descending(client: TestClient) -> None:
    rows = client.get("/api/rainfall/comparison").json()
    pcts = [r["change_percent"] for r in rows]
    assert pcts == sorted(pcts, reverse=True)


# ---------------------------------------------------------------------------
# /api/rainfall/anomalies
# ---------------------------------------------------------------------------

def test_anomalies_relative_to_baseline(
    client: TestClient, db: sqlite3.Connection
) -> None:
    baseline = (1961, 1990)
    response = client.get(
        "/api/rainfall/anomalies",
        params={"baseline_start": baseline[0], "baseline_end": baseline[1]},
    )
    assert response.status_code == 200
    api = {(r["station_name"], r["year"]): r for r in response.json()}

    name_by_id = {sid: name for name, sid in _station_id_by_name(db).items()}
    annual_all = _annual_totals(db, 1, 9999)
    annual_baseline = _annual_totals(db, *baseline)

    baseline_avg: dict[int, float] = {}
    bucket: dict[int, list[float]] = {}
    for (sid, _), total in annual_baseline.items():
        bucket.setdefault(sid, []).append(total)
    for sid, vals in bucket.items():
        baseline_avg[sid] = sum(vals) / len(vals)

    sample = list(annual_all.items())[::500]  # spot-check a slice
    for (sid, year), total in sample:
        name = name_by_id[sid]
        if (name, year) not in api:
            continue
        row = api[(name, year)]
        assert row["annual_total"] == _approx(total)
        assert row["baseline_avg"] == _approx(baseline_avg[sid])
        assert row["anomaly"] == _approx(total - baseline_avg[sid])
        assert row["anomaly_percent"] == _approx(
            (total - baseline_avg[sid]) / baseline_avg[sid] * 100
        )


def test_anomalies_baseline_period_centered_on_zero(
    client: TestClient, db: sqlite3.Connection
) -> None:
    """For each station the anomalies in the baseline window must average to ~0."""
    baseline = (1961, 1990)
    rows = client.get(
        "/api/rainfall/anomalies",
        params={"baseline_start": baseline[0], "baseline_end": baseline[1]},
    ).json()

    by_station: dict[str, list[float]] = {}
    for r in rows:
        if baseline[0] <= r["year"] <= baseline[1]:
            by_station.setdefault(r["station_name"], []).append(r["anomaly"])

    for station, anomalies in by_station.items():
        if not anomalies:
            continue
        assert abs(sum(anomalies) / len(anomalies)) < 1e-6, station


# ---------------------------------------------------------------------------
# /api/rainfall/trends — moving-average is computed in Python; sanity check it
# ---------------------------------------------------------------------------

def test_trends_moving_average_matches_window(
    client: TestClient, db: sqlite3.Connection
) -> None:
    window = 10
    rows = client.get("/api/rainfall/trends", params={"window": window}).json()
    assert all(rows[i]["moving_avg"] is None for i in range(window - 1))
    for i in range(window - 1, len(rows)):
        slice_ = [rows[j]["national_avg"] for j in range(i - window + 1, i + 1)]
        assert rows[i]["moving_avg"] == _approx(sum(slice_) / window)
