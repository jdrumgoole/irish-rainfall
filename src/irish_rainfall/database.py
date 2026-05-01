"""Database operations for Irish rainfall data."""

import sqlite3
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "rainfall.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection with read-friendly pragmas applied.

    Args:
        db_path: Path to the SQLite database. Defaults to data/rainfall.db.

    Returns:
        SQLite connection object.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA cache_size=-20000")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """Create (or migrate) the schema and statistics.

    Idempotent: drops obsolete single-column rainfall indexes, installs the
    composite covering index used by every aggregation query, and refreshes
    sqlite_stat1 via ANALYZE so the planner picks the right index.

    Args:
        conn: SQLite connection object.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            county TEXT,
            easting REAL,
            northing REAL,
            latitude REAL,
            longitude REAL,
            elevation_metres REAL
        );

        CREATE TABLE IF NOT EXISTS rainfall (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            amount_mm REAL,
            FOREIGN KEY (station_id) REFERENCES stations(id),
            UNIQUE (station_id, year, month)
        );

        DROP INDEX IF EXISTS idx_rainfall_station;
        DROP INDEX IF EXISTS idx_rainfall_year;
        DROP INDEX IF EXISTS idx_rainfall_month;

        CREATE INDEX IF NOT EXISTS idx_rainfall_station_year_month
            ON rainfall(station_id, year, month, amount_mm);

        ANALYZE;
    """)
    conn.commit()


def insert_station(
    conn: sqlite3.Connection,
    name: str,
    county: Optional[str],
    easting: Optional[float],
    northing: Optional[float],
    latitude: Optional[float],
    longitude: Optional[float],
    elevation_metres: Optional[float],
) -> int:
    """Insert a station into the database.

    Args:
        conn: SQLite connection object.
        name: Station name.
        county: County name.
        easting: Irish Grid easting coordinate.
        northing: Irish Grid northing coordinate.
        latitude: Latitude in decimal degrees.
        longitude: Longitude in decimal degrees.
        elevation_metres: Elevation in metres.

    Returns:
        The ID of the inserted station.
    """
    cursor = conn.execute(
        """
        INSERT INTO stations (name, county, easting, northing, latitude, longitude, elevation_metres)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, county, easting, northing, latitude, longitude, elevation_metres),
    )
    conn.commit()
    return cursor.lastrowid


def insert_rainfall(
    conn: sqlite3.Connection,
    station_id: int,
    year: int,
    month: int,
    amount_mm: Optional[float],
) -> int:
    """Insert a rainfall measurement into the database.

    Args:
        conn: SQLite connection object.
        station_id: ID of the station.
        year: Year of the measurement.
        month: Month of the measurement (1-12).
        amount_mm: Rainfall amount in millimetres.

    Returns:
        The ID of the inserted rainfall record.
    """
    cursor = conn.execute(
        """
        INSERT INTO rainfall (station_id, year, month, amount_mm)
        VALUES (?, ?, ?, ?)
        """,
        (station_id, year, month, amount_mm),
    )
    return cursor.lastrowid


def insert_rainfall_batch(
    conn: sqlite3.Connection,
    records: list[tuple[int, int, int, Optional[float]]],
) -> None:
    """Insert multiple rainfall measurements into the database.

    Args:
        conn: SQLite connection object.
        records: List of (station_id, year, month, amount_mm) tuples.
    """
    conn.executemany(
        """
        INSERT INTO rainfall (station_id, year, month, amount_mm)
        VALUES (?, ?, ?, ?)
        """,
        records,
    )
    conn.commit()
