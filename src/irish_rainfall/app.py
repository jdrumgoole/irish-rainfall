"""FastAPI application for Irish Rainfall data visualization."""

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from irish_rainfall.database import DEFAULT_DB_PATH

app = FastAPI(
    title="Irish Rainfall Dashboard",
    description="Visualization of 160 years of Irish precipitation data (1850-2010)",
    version="0.1.0",
)

# Set up templates
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Create directories if they don't exist
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_db_connection() -> sqlite3.Connection:
    """Get a database connection."""
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Serve the main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/stations")
async def get_stations() -> list[dict]:
    """Get all weather stations with their metadata."""
    conn = get_db_connection()
    cursor = conn.execute("""
        SELECT id, name, county, latitude, longitude, elevation_metres,
               easting, northing
        FROM stations
        ORDER BY name
    """)
    stations = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return stations


@app.get("/api/stations/{station_id}")
async def get_station(station_id: int) -> dict:
    """Get a single station by ID."""
    conn = get_db_connection()
    cursor = conn.execute("""
        SELECT id, name, county, latitude, longitude, elevation_metres,
               easting, northing
        FROM stations
        WHERE id = ?
    """, (station_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"error": "Station not found"}


@app.get("/api/rainfall/annual")
async def get_annual_rainfall(
    station_id: Optional[int] = Query(None, description="Filter by station ID"),
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get annual rainfall totals."""
    conn = get_db_connection()

    if station_id:
        cursor = conn.execute("""
            SELECT s.name as station_name, r.year, SUM(r.amount_mm) as annual_total
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ? AND r.year BETWEEN ? AND ?
            GROUP BY r.station_id, r.year
            ORDER BY r.year
        """, (station_id, start_year, end_year))
    else:
        # National average (average of all stations per year)
        cursor = conn.execute("""
            SELECT 'National Average' as station_name, year,
                   AVG(annual_total) as annual_total
            FROM (
                SELECT station_id, year, SUM(amount_mm) as annual_total
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, year
            )
            GROUP BY year
            ORDER BY year
        """, (start_year, end_year))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


@app.get("/api/rainfall/monthly")
async def get_monthly_rainfall(
    station_id: Optional[int] = Query(None, description="Filter by station ID"),
    year: Optional[int] = Query(None, description="Filter by year"),
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get monthly rainfall data."""
    conn = get_db_connection()

    if station_id and year:
        cursor = conn.execute("""
            SELECT s.name as station_name, r.year, r.month, r.amount_mm
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ? AND r.year = ?
            ORDER BY r.month
        """, (station_id, year))
    elif station_id:
        cursor = conn.execute("""
            SELECT s.name as station_name, r.year, r.month, r.amount_mm
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ? AND r.year BETWEEN ? AND ?
            ORDER BY r.year, r.month
        """, (station_id, start_year, end_year))
    else:
        # National monthly average
        cursor = conn.execute("""
            SELECT 'National Average' as station_name, year, month,
                   AVG(amount_mm) as amount_mm
            FROM rainfall
            WHERE year BETWEEN ? AND ?
            GROUP BY year, month
            ORDER BY year, month
        """, (start_year, end_year))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


@app.get("/api/rainfall/seasonal")
async def get_seasonal_rainfall(
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get seasonal rainfall averages by station."""
    conn = get_db_connection()

    # Seasons: Winter (DJF), Spring (MAM), Summer (JJA), Autumn (SON)
    cursor = conn.execute("""
        SELECT
            s.name as station_name,
            s.id as station_id,
            CASE
                WHEN r.month IN (12, 1, 2) THEN 'Winter'
                WHEN r.month IN (3, 4, 5) THEN 'Spring'
                WHEN r.month IN (6, 7, 8) THEN 'Summer'
                WHEN r.month IN (9, 10, 11) THEN 'Autumn'
            END as season,
            AVG(r.amount_mm) as avg_monthly_rainfall
        FROM rainfall r
        JOIN stations s ON r.station_id = s.id
        WHERE r.year BETWEEN ? AND ?
        GROUP BY s.id, season
        ORDER BY s.name,
            CASE season
                WHEN 'Winter' THEN 1
                WHEN 'Spring' THEN 2
                WHEN 'Summer' THEN 3
                WHEN 'Autumn' THEN 4
            END
    """, (start_year, end_year))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


@app.get("/api/rainfall/climatology")
async def get_climatology(
    start_year: int = Query(1850, description="Start year"),
    end_year: int = Query(2010, description="End year"),
) -> list[dict]:
    """Get monthly climatology (average by month) for all stations."""
    conn = get_db_connection()

    cursor = conn.execute("""
        SELECT
            s.name as station_name,
            s.id as station_id,
            r.month,
            AVG(r.amount_mm) as avg_rainfall
        FROM rainfall r
        JOIN stations s ON r.station_id = s.id
        WHERE r.year BETWEEN ? AND ?
        GROUP BY s.id, r.month
        ORDER BY s.name, r.month
    """, (start_year, end_year))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


@app.get("/api/rainfall/station-summary")
async def get_station_summary() -> list[dict]:
    """Get summary statistics for each station."""
    conn = get_db_connection()

    cursor = conn.execute("""
        SELECT
            s.id,
            s.name,
            s.county,
            s.latitude,
            s.longitude,
            s.elevation_metres,
            AVG(annual.total) as avg_annual_rainfall,
            MIN(annual.total) as min_annual_rainfall,
            MAX(annual.total) as max_annual_rainfall,
            COUNT(DISTINCT annual.year) as years_of_data
        FROM stations s
        JOIN (
            SELECT station_id, year, SUM(amount_mm) as total
            FROM rainfall
            GROUP BY station_id, year
        ) annual ON s.id = annual.station_id
        GROUP BY s.id
        ORDER BY avg_annual_rainfall DESC
    """)

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


@app.get("/api/rainfall/trends")
async def get_trends(
    window: int = Query(10, description="Moving average window in years"),
) -> list[dict]:
    """Get rainfall trends with moving average."""
    conn = get_db_connection()

    # Get annual totals for national average
    cursor = conn.execute("""
        SELECT year, AVG(annual_total) as national_avg
        FROM (
            SELECT station_id, year, SUM(amount_mm) as annual_total
            FROM rainfall
            GROUP BY station_id, year
        )
        GROUP BY year
        ORDER BY year
    """)

    data = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # Calculate moving average
    for i, row in enumerate(data):
        if i >= window - 1:
            values = [data[j]["national_avg"] for j in range(i - window + 1, i + 1)]
            row["moving_avg"] = sum(values) / len(values)
        else:
            row["moving_avg"] = None

    return data


@app.get("/api/rainfall/anomalies")
async def get_anomalies(
    baseline_start: int = Query(1961, description="Baseline period start"),
    baseline_end: int = Query(1990, description="Baseline period end"),
) -> list[dict]:
    """Get rainfall anomalies relative to a baseline period."""
    conn = get_db_connection()

    # Calculate baseline averages per station
    cursor = conn.execute("""
        WITH baseline AS (
            SELECT station_id, AVG(annual_total) as baseline_avg
            FROM (
                SELECT station_id, year, SUM(amount_mm) as annual_total
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, year
            )
            GROUP BY station_id
        ),
        annual AS (
            SELECT station_id, year, SUM(amount_mm) as annual_total
            FROM rainfall
            GROUP BY station_id, year
        )
        SELECT
            s.name as station_name,
            a.year,
            a.annual_total,
            b.baseline_avg,
            (a.annual_total - b.baseline_avg) as anomaly,
            ((a.annual_total - b.baseline_avg) / b.baseline_avg * 100) as anomaly_percent
        FROM annual a
        JOIN stations s ON a.station_id = s.id
        JOIN baseline b ON a.station_id = b.station_id
        ORDER BY a.year, s.name
    """, (baseline_start, baseline_end))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


@app.get("/api/rainfall/comparison")
async def get_period_comparison(
    period1_start: int = Query(1850, description="First period start"),
    period1_end: int = Query(1900, description="First period end"),
    period2_start: int = Query(1960, description="Second period start"),
    period2_end: int = Query(2010, description="Second period end"),
) -> list[dict]:
    """Compare rainfall between two time periods."""
    conn = get_db_connection()

    cursor = conn.execute("""
        WITH period1 AS (
            SELECT station_id, AVG(annual_total) as avg_rainfall
            FROM (
                SELECT station_id, year, SUM(amount_mm) as annual_total
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, year
            )
            GROUP BY station_id
        ),
        period2 AS (
            SELECT station_id, AVG(annual_total) as avg_rainfall
            FROM (
                SELECT station_id, year, SUM(amount_mm) as annual_total
                FROM rainfall
                WHERE year BETWEEN ? AND ?
                GROUP BY station_id, year
            )
            GROUP BY station_id
        )
        SELECT
            s.name as station_name,
            s.latitude,
            s.longitude,
            p1.avg_rainfall as period1_avg,
            p2.avg_rainfall as period2_avg,
            (p2.avg_rainfall - p1.avg_rainfall) as change,
            ((p2.avg_rainfall - p1.avg_rainfall) / p1.avg_rainfall * 100) as change_percent
        FROM stations s
        JOIN period1 p1 ON s.id = p1.station_id
        JOIN period2 p2 ON s.id = p2.station_id
        ORDER BY change_percent DESC
    """, (period1_start, period1_end, period2_start, period2_end))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return results


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

    # Add station info to each change point
    for cp in changepoints:
        cp["station_name"] = station_name

    return changepoints
