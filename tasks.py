"""Invoke tasks for the Irish Rainfall project."""

import signal
import sys
from pathlib import Path

from invoke import task
from invoke.context import Context

# Project paths
PROJECT_ROOT = Path(__file__).parent
SRC_DIR = PROJECT_ROOT / "src"
DATA_DIR = PROJECT_ROOT / "data"


@task
def start(ctx: Context, host: str = "127.0.0.1", port: int = 8000, reload: bool = True) -> None:
    """Start the FastAPI development server.

    Args:
        ctx: Invoke context.
        host: Host to bind to. Defaults to 127.0.0.1.
        port: Port to bind to. Defaults to 8000.
        reload: Enable auto-reload on code changes. Defaults to True.
    """
    reload_flag = "--reload" if reload else ""
    cmd = f"uv run uvicorn irish_rainfall.app:app --host {host} --port {port} {reload_flag}"
    print(f"Starting server at http://{host}:{port}")
    print("Press Ctrl+C to stop")
    ctx.run(cmd, pty=True)


@task
def stop(ctx: Context, port: int = 8000) -> None:
    """Stop any server running on the specified port.

    Args:
        ctx: Invoke context.
        port: Port to check for running server. Defaults to 8000.
    """
    ctx.run(f"lsof -ti:{port} | xargs kill -9 2>/dev/null || echo 'No server running on port {port}'")


@task
def restart(ctx: Context, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Restart the FastAPI server.

    Args:
        ctx: Invoke context.
        host: Host to bind to. Defaults to 127.0.0.1.
        port: Port to bind to. Defaults to 8000.
    """
    stop(ctx, port)
    start(ctx, host, port)


@task
def import_data(ctx: Context, source: str = "", force: bool = False) -> None:
    """Import rainfall data into SQLite database.

    By default, downloads data from Met Éireann. Can also import from a local directory.

    Args:
        ctx: Invoke context.
        source: Path to local directory with CSV files. If empty, downloads from Met Éireann.
        force: Force re-import even if database already exists.
    """
    force_flag = "--force" if force else ""
    if source:
        ctx.run(f"uv run python -m irish_rainfall.import_data {source} {force_flag}", pty=True)
    else:
        ctx.run(f"uv run python -m irish_rainfall.import_data {force_flag}", pty=True)


@task
def test(ctx: Context, verbose: bool = False) -> None:
    """Run the test suite.

    Args:
        ctx: Invoke context.
        verbose: Enable verbose output. Defaults to False.
    """
    prepare_test_data(ctx)
    verbose_flag = "-v" if verbose else ""
    ctx.run(f"uv run pytest {verbose_flag}", pty=True)


@task
def prepare_test_data(ctx: Context, force: bool = False) -> None:
    """Stage the production rainfall.db at tests/data/rainfall.db for the test suite.

    The test database is a copy of data/rainfall.db, kept out of git.
    If data/rainfall.db is missing the importer is run first.

    Args:
        ctx: Invoke context.
        force: Re-copy even if tests/data/rainfall.db already exists.
    """
    src = DATA_DIR / "rainfall.db"
    dst_dir = PROJECT_ROOT / "tests" / "data"
    dst = dst_dir / "rainfall.db"

    if dst.exists() and not force:
        print(f"Test database already present: {dst}")
        return

    if not src.exists():
        print(f"Source database missing at {src} -- running importer")
        ctx.run("uv run python -m irish_rainfall.import_data", pty=True)

    if not src.exists():
        raise RuntimeError(f"Importer did not produce {src}")

    dst_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(src, dst)

    # Apply current schema (composite index, ANALYZE) to the staged copy.
    import sqlite3
    sys.path.insert(0, str(SRC_DIR))
    from irish_rainfall.database import create_tables
    conn = sqlite3.connect(dst)
    try:
        create_tables(conn)
    finally:
        conn.close()
    print(f"Copied and migrated {src} -> {dst}")


@task
def migrate(ctx: Context, db: str = "") -> None:
    """Apply the current schema (indexes, ANALYZE) to an existing database.

    Args:
        ctx: Invoke context.
        db: Path to the SQLite database. Defaults to data/rainfall.db.
    """
    import sqlite3
    sys.path.insert(0, str(SRC_DIR))
    from irish_rainfall.database import DEFAULT_DB_PATH, create_tables
    db_path = Path(db) if db else DEFAULT_DB_PATH
    if not db_path.exists():
        raise RuntimeError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        create_tables(conn)
    finally:
        conn.close()
    print(f"Migrated schema applied to {db_path}")


@task
def lint(ctx: Context) -> None:
    """Run code linting with ruff.

    Args:
        ctx: Invoke context.
    """
    ctx.run("uv run ruff check src/ tests/", pty=True)


@task
def format_code(ctx: Context) -> None:
    """Format code with ruff.

    Args:
        ctx: Invoke context.
    """
    ctx.run("uv run ruff format src/ tests/", pty=True)


@task
def clean(ctx: Context) -> None:
    """Clean up build artifacts and caches.

    Args:
        ctx: Invoke context.
    """
    patterns = [
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".pytest_cache",
        ".ruff_cache",
        "*.egg-info",
        "dist",
        "build",
    ]
    for pattern in patterns:
        ctx.run(f"find . -name '{pattern}' -type d -exec rm -rf {{}} + 2>/dev/null || true")
        ctx.run(f"find . -name '{pattern}' -type f -delete 2>/dev/null || true")
    print("Cleaned up build artifacts")


@task
def status(ctx: Context, port: int = 8000) -> None:
    """Check if the server is running.

    Args:
        ctx: Invoke context.
        port: Port to check. Defaults to 8000.
    """
    result = ctx.run(f"lsof -ti:{port}", warn=True, hide=True)
    if result.ok and result.stdout.strip():
        pid = result.stdout.strip().split()[0]
        print(f"Server is running on port {port} (PID: {pid})")
    else:
        print(f"No server running on port {port}")


@task
def db_info(ctx: Context) -> None:
    """Show database statistics.

    Args:
        ctx: Invoke context.
    """
    script = """
import sqlite3
from pathlib import Path
db_path = Path('data/rainfall.db')
if not db_path.exists():
    print('Database not found. Run: invoke import-data')
else:
    conn = sqlite3.connect(db_path)
    stations = conn.execute('SELECT COUNT(*) FROM stations').fetchone()[0]
    records = conn.execute('SELECT COUNT(*) FROM rainfall').fetchone()[0]
    years = conn.execute('SELECT MIN(year), MAX(year) FROM rainfall').fetchone()
    print(f'Database: {db_path}')
    print(f'Stations: {stations}')
    print(f'Rainfall records: {records:,}')
    print(f'Year range: {years[0]} - {years[1]}')
    conn.close()
"""
    ctx.run(f'uv run python -c "{script}"', pty=True)


@task
def changepoints(
    ctx: Context,
    station: int = 0,
    all_stations: bool = False,
    export_only: bool = False,
) -> None:
    """Detect change points in rainfall data using Apache Otava.

    Uses statistical analysis to find significant shifts in rainfall patterns.
    Requires Apache Otava to be installed: pipx install git+https://github.com/apache/otava.git

    Args:
        ctx: Invoke context.
        station: Station ID to analyze (0 = national average).
        all_stations: Analyze all stations.
        export_only: Only export CSV data, don't run analysis.
    """
    cmd = "uv run python -m irish_rainfall.changepoint"

    if station > 0:
        cmd += f" --station {station}"
    elif all_stations:
        cmd += " --all-stations"

    if export_only:
        cmd += " --export-only"

    ctx.run(cmd, pty=True)
