"""FastAPI application for Irish Rainfall data visualization."""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from irish_rainfall.database import DEFAULT_DB_PATH, create_tables, get_connection

logger = logging.getLogger("uvicorn.error")


def _warm_cache() -> None:
    """Pre-populate lru_cache for the queries the dashboard fires on first load."""
    db = str(DEFAULT_DB_PATH)
    t0 = time.perf_counter()
    _stations(db)
    _annual_national(db, 1850, 2010)
    _climatology(db, 1850, 2010)
    _seasonal(db, 1850, 2010)
    _station_summary(db, 1850, 2010)
    _comparison(db, 1850, 1900, 1960, 2010)
    _trends(db, 10)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info("Warmed query cache in %.1f ms", elapsed_ms)


@asynccontextmanager
async def lifespan(_: FastAPI):
    conn = get_connection(DEFAULT_DB_PATH)
    try:
        create_tables(conn)
    finally:
        conn.close()
    _warm_cache()
    yield


app = FastAPI(
    title="Irish Rainfall Dashboard",
    description="Visualization of 160 years of Irish precipitation data (1850-2010)",
    version="0.4.0",
    lifespan=lifespan,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _conn() -> sqlite3.Connection:
    return get_connection(DEFAULT_DB_PATH)


def _rows(cursor: sqlite3.Cursor) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in cursor.fetchall())


# ---------------------------------------------------------------------------
# Cached query layer
#
# The IIP dataset ends in 2010 and never changes during a server's lifetime,
# so every query function below is memoised by (db_path, *params). Returning
# tuples means callers cannot accidentally mutate the cache. Endpoints copy
# to list before applying any mutation (only `trends` does so).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _stations(db_path: str) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT id, name, county, latitude, longitude, elevation_metres,
                   easting, northing
            FROM stations
            ORDER BY name
        """))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _station(db_path: str, station_id: int) -> Optional[dict[str, Any]]:
    conn = _conn()
    try:
        row = conn.execute("""
            SELECT id, name, county, latitude, longitude, elevation_metres,
                   easting, northing
            FROM stations
            WHERE id = ?
        """, (station_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


@lru_cache(maxsize=128)
def _annual_one_station(
    db_path: str, station_id: int, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT s.name AS station_name, r.year,
                   SUM(r.amount_mm) AS annual_total
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ? AND r.year BETWEEN ? AND ?
            GROUP BY r.station_id, r.year
            ORDER BY r.year
        """, (station_id, start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _annual_national(
    db_path: str, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT 'National Average' AS station_name, year,
                   AVG(annual_total) AS annual_total
            FROM (
                SELECT station_id, year, SUM(amount_mm) AS annual_total
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, year
            )
            GROUP BY year
            ORDER BY year
        """, (start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=128)
def _monthly_one_station_year(
    db_path: str, station_id: int, year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT s.name AS station_name, r.year, r.month, r.amount_mm
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ? AND r.year = ?
            ORDER BY r.month
        """, (station_id, year)))
    finally:
        conn.close()


@lru_cache(maxsize=128)
def _monthly_one_station(
    db_path: str, station_id: int, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT s.name AS station_name, r.year, r.month, r.amount_mm
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ? AND r.year BETWEEN ? AND ?
            ORDER BY r.year, r.month
        """, (station_id, start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _monthly_national(
    db_path: str, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT 'National Average' AS station_name, year, month,
                   AVG(amount_mm) AS amount_mm
            FROM rainfall
            WHERE year BETWEEN ? AND ?
            GROUP BY year, month
            ORDER BY year, month
        """, (start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _seasonal(
    db_path: str, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            WITH agg AS (
                SELECT station_id,
                       CASE
                           WHEN month IN (12, 1, 2)  THEN 'Winter'
                           WHEN month IN (3, 4, 5)   THEN 'Spring'
                           WHEN month IN (6, 7, 8)   THEN 'Summer'
                           WHEN month IN (9, 10, 11) THEN 'Autumn'
                       END AS season,
                       AVG(amount_mm) AS avg_monthly_rainfall
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, season
            )
            SELECT s.name AS station_name,
                   s.id   AS station_id,
                   agg.season,
                   agg.avg_monthly_rainfall
            FROM agg
            JOIN stations s ON s.id = agg.station_id
            ORDER BY s.name,
                CASE agg.season
                    WHEN 'Winter' THEN 1
                    WHEN 'Spring' THEN 2
                    WHEN 'Summer' THEN 3
                    WHEN 'Autumn' THEN 4
                END
        """, (start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _climatology(
    db_path: str, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            WITH agg AS (
                SELECT station_id, month, AVG(amount_mm) AS avg_rainfall
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, month
            )
            SELECT s.name AS station_name,
                   s.id   AS station_id,
                   agg.month,
                   agg.avg_rainfall
            FROM agg
            JOIN stations s ON s.id = agg.station_id
            ORDER BY s.name, agg.month
        """, (start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _station_summary(
    db_path: str, start_year: int, end_year: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            SELECT s.id, s.name, s.county, s.latitude, s.longitude,
                   s.elevation_metres,
                   AVG(annual.total) AS avg_annual_rainfall,
                   MIN(annual.total) AS min_annual_rainfall,
                   MAX(annual.total) AS max_annual_rainfall,
                   COUNT(DISTINCT annual.year) AS years_of_data
            FROM stations s
            JOIN (
                SELECT station_id, year, SUM(amount_mm) AS total
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, year
            ) annual ON s.id = annual.station_id
            GROUP BY s.id
            ORDER BY avg_annual_rainfall DESC
        """, (start_year, end_year)))
    finally:
        conn.close()


@lru_cache(maxsize=64)
def _trends(db_path: str, window: int) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        rows = [dict(r) for r in conn.execute("""
            SELECT year, AVG(annual_total) AS national_avg
            FROM (
                SELECT station_id, year, SUM(amount_mm) AS annual_total
                FROM rainfall
                GROUP BY station_id, year
            )
            GROUP BY year
            ORDER BY year
        """).fetchall()]
    finally:
        conn.close()

    for i, row in enumerate(rows):
        if i >= window - 1:
            slice_ = [rows[j]["national_avg"] for j in range(i - window + 1, i + 1)]
            row["moving_avg"] = sum(slice_) / window
        else:
            row["moving_avg"] = None
    return tuple(rows)


@lru_cache(maxsize=32)
def _anomalies(
    db_path: str, baseline_start: int, baseline_end: int
) -> tuple[dict[str, Any], ...]:
    conn = _conn()
    try:
        return _rows(conn.execute("""
            WITH baseline AS (
                SELECT station_id, AVG(annual_total) AS baseline_avg
                FROM (
                    SELECT station_id, year, SUM(amount_mm) AS annual_total
                    FROM rainfall
                    WHERE year BETWEEN ? AND ?
                    GROUP BY station_id, year
                )
                GROUP BY station_id
            ),
            annual AS (
                SELECT station_id, year, SUM(amount_mm) AS annual_total
                FROM rainfall
                GROUP BY station_id, year
            )
            SELECT s.name AS station_name,
                   a.year,
                   a.annual_total,
                   b.baseline_avg,
                   (a.annual_total - b.baseline_avg) AS anomaly,
                   ((a.annual_total - b.baseline_avg) / b.baseline_avg * 100)
                       AS anomaly_percent
            FROM annual a
            JOIN stations s  ON a.station_id = s.id
            JOIN baseline b  ON a.station_id = b.station_id
            ORDER BY a.year, s.name
        """, (baseline_start, baseline_end)))
    finally:
        conn.close()


@lru_cache(maxsize=32)
def _comparison(
    db_path: str,
    p1_start: int, p1_end: int,
    p2_start: int, p2_end: int,
) -> tuple[dict[str, Any], ...]:
    """Single-pass conditional aggregation: rainfall is scanned once instead of twice."""
    conn = _conn()
    try:
        return _rows(conn.execute("""
            WITH annual AS (
                SELECT station_id, year, SUM(amount_mm) AS total
                FROM rainfall
                WHERE (year BETWEEN :p1s AND :p1e)
                   OR (year BETWEEN :p2s AND :p2e)
                GROUP BY station_id, year
            ),
            agg AS (
                SELECT station_id,
                       AVG(CASE WHEN year BETWEEN :p1s AND :p1e THEN total END) AS p1,
                       AVG(CASE WHEN year BETWEEN :p2s AND :p2e THEN total END) AS p2
                FROM annual
                GROUP BY station_id
            )
            SELECT s.name AS station_name,
                   s.latitude, s.longitude,
                   agg.p1 AS period1_avg,
                   agg.p2 AS period2_avg,
                   (agg.p2 - agg.p1) AS change,
                   ((agg.p2 - agg.p1) / agg.p1 * 100) AS change_percent
            FROM stations s
            JOIN agg ON s.id = agg.station_id
            WHERE agg.p1 IS NOT NULL AND agg.p2 IS NOT NULL
            ORDER BY change_percent DESC
        """, {"p1s": p1_start, "p1e": p1_end, "p2s": p2_start, "p2e": p2_end}))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Serve the main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/stations")
async def get_stations() -> list[dict]:
    """Get all weather stations with their metadata."""
    return list(_stations(str(DEFAULT_DB_PATH)))


@app.get("/api/stations/{station_id}")
async def get_station(station_id: int) -> dict:
    """Get a single station by ID."""
    row = _station(str(DEFAULT_DB_PATH), station_id)
    return row if row else {"error": "Station not found"}


@app.get("/api/rainfall/annual")
async def get_annual_rainfall(
    station_id: Optional[int] = Query(None, description="Filter by station ID"),
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get annual rainfall totals."""
    db = str(DEFAULT_DB_PATH)
    if station_id:
        return list(_annual_one_station(db, station_id, start_year, end_year))
    return list(_annual_national(db, start_year, end_year))


@app.get("/api/rainfall/monthly")
async def get_monthly_rainfall(
    station_id: Optional[int] = Query(None, description="Filter by station ID"),
    year: Optional[int] = Query(None, description="Filter by year"),
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get monthly rainfall data."""
    db = str(DEFAULT_DB_PATH)
    if station_id and year:
        return list(_monthly_one_station_year(db, station_id, year))
    if station_id:
        return list(_monthly_one_station(db, station_id, start_year, end_year))
    return list(_monthly_national(db, start_year, end_year))


@app.get("/api/rainfall/seasonal")
async def get_seasonal_rainfall(
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get seasonal rainfall averages by station."""
    return list(_seasonal(str(DEFAULT_DB_PATH), start_year, end_year))


@app.get("/api/rainfall/climatology")
async def get_climatology(
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get monthly climatology (average by month) for all stations."""
    return list(_climatology(str(DEFAULT_DB_PATH), start_year, end_year))


@app.get("/api/rainfall/station-summary")
async def get_station_summary(
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get summary statistics for each station over a year range."""
    return list(_station_summary(str(DEFAULT_DB_PATH), start_year, end_year))


@app.get("/api/rainfall/trends")
async def get_trends(
    window: int = Query(10, description="Moving average window in years"),
) -> list[dict]:
    """Get rainfall trends with moving average."""
    return [dict(r) for r in _trends(str(DEFAULT_DB_PATH), window)]


@app.get("/api/rainfall/anomalies")
async def get_anomalies(
    baseline_start: int = Query(1961, description="Baseline period start"),
    baseline_end: int = Query(1990, description="Baseline period end"),
) -> list[dict]:
    """Get rainfall anomalies relative to a baseline period."""
    return list(_anomalies(str(DEFAULT_DB_PATH), baseline_start, baseline_end))


@app.get("/api/rainfall/comparison")
async def get_period_comparison(
    period1_start: int = Query(1850, description="First period start"),
    period1_end: int = Query(1900, description="First period end"),
    period2_start: int = Query(1960, description="Second period start"),
    period2_end: int = Query(2010, description="Second period end"),
) -> list[dict]:
    """Compare rainfall between two time periods."""
    return list(_comparison(
        str(DEFAULT_DB_PATH),
        period1_start, period1_end,
        period2_start, period2_end,
    ))


@app.get("/api/rainfall/changepoints")
async def get_changepoints(
    station_id: Optional[int] = Query(None, description="Station ID (None for national average)"),
    penalty: float = Query(3.0, description="Penalty for change point detection (higher = fewer points)"),
) -> list[dict]:
    """Detect change points in rainfall data using ruptures PELT algorithm.

    Returns statistically significant shifts in rainfall patterns.
    """
    from irish_rainfall.changepoint import detect_changepoints_ruptures, get_annual_rainfall_data

    years, values, station_name = get_annual_rainfall_data(None, station_id)

    if len(values) < 20:
        return []

    changepoints = detect_changepoints_ruptures(values, years, penalty=penalty)

    for cp in changepoints:
        cp["station_name"] = station_name

    return changepoints
