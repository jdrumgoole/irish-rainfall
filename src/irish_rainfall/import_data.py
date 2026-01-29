"""Import rainfall data from CSV files into SQLite database."""

import argparse
import csv
import io
import shutil
import signal
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from irish_rainfall.database import (
    DEFAULT_DB_PATH,
    create_tables,
    get_connection,
    insert_rainfall_batch,
    insert_station,
)


# Default data source URL (25-station network, 1850-2010)
DATA_URL = "https://www.met.ie/cms/assets/uploads/2018/01/Long-Term-IIP-network-1.zip"

# Default directory for raw data
RAW_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw"


def parse_float(value: str) -> Optional[float]:
    """Parse a float value, returning None for empty or invalid values.

    Args:
        value: String value to parse.

    Returns:
        Parsed float or None if invalid/empty.
    """
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_station_csv(csv_path: Path) -> tuple[dict, list[dict]]:
    """Parse a station CSV file from a file path.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        Tuple of (station_metadata, list of rainfall records).
    """
    with open(csv_path, "r", encoding="utf-8") as f:
        return parse_station_csv_content(f, csv_path.stem)


def parse_station_csv_content(file_obj: io.TextIOBase, filename: str) -> tuple[dict, list[dict]]:
    """Parse a station CSV file from a file object.

    Args:
        file_obj: File-like object containing CSV data.
        filename: Name of the file (used as fallback station name).

    Returns:
        Tuple of (station_metadata, list of rainfall records).
    """
    reader = csv.reader(file_obj)
    rows = list(reader)

    # Row 0: Header names
    # Row 1: Station metadata values
    # Row 2: Data column headers (Year, Jan, Feb, ...)
    # Row 3+: Rainfall data

    metadata_values = rows[1]

    station = {
        "name": metadata_values[5].strip() if len(metadata_values) > 5 else filename,
        "county": metadata_values[6].strip() if len(metadata_values) > 6 and metadata_values[6].strip() else None,
        "easting": parse_float(metadata_values[0]) if len(metadata_values) > 0 else None,
        "northing": parse_float(metadata_values[1]) if len(metadata_values) > 1 else None,
        "latitude": parse_float(metadata_values[2]) if len(metadata_values) > 2 else None,
        "longitude": parse_float(metadata_values[3]) if len(metadata_values) > 3 else None,
        "elevation_metres": parse_float(metadata_values[4]) if len(metadata_values) > 4 else None,
    }

    rainfall_records = []
    for row in rows[3:]:  # Skip header rows, start from data
        if not row or not row[0].strip():
            continue
        try:
            year = int(row[0])
        except ValueError:
            continue

        for month_idx in range(1, 13):
            col_idx = month_idx  # Year is column 0, Jan is column 1, etc.
            if col_idx < len(row):
                amount = parse_float(row[col_idx])
                rainfall_records.append({
                    "year": year,
                    "month": month_idx,
                    "amount_mm": amount,
                })

    return station, rainfall_records


def download_data(url: str) -> bytes:
    """Download data from a URL.

    Args:
        url: URL to download from.

    Returns:
        Downloaded data as bytes.
    """
    print(f"Downloading data from: {url}")

    # Create a request with a User-Agent header
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (irish-rainfall-importer)"}
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        total_size = response.headers.get("Content-Length")
        if total_size:
            print(f"File size: {int(total_size) / 1024 / 1024:.1f} MB")

        data = response.read()
        print(f"Downloaded {len(data) / 1024 / 1024:.1f} MB")
        return data


def extract_zip_to_directory(zip_data: bytes, output_dir: Path) -> Path:
    """Extract a zip file to a directory.

    Args:
        zip_data: Zip file contents as bytes.
        output_dir: Directory to extract to. Will be created/overwritten.

    Returns:
        Path to the directory containing the extracted files.
    """
    # Remove existing directory if it exists
    if output_dir.exists():
        print(f"Removing existing data directory: {output_dir}")
        shutil.rmtree(output_dir)

    # Create the output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Extracting to: {output_dir}")

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        # Get all files, skipping macOS metadata
        for member in zf.namelist():
            if member.startswith("__MACOSX"):
                continue

            # Get the filename without any directory prefix
            filename = Path(member).name
            if not filename:  # Skip directory entries
                continue

            # Extract to flat directory structure
            target_path = output_dir / filename
            with zf.open(member) as source, open(target_path, "wb") as target:
                target.write(source.read())
            print(f"  Extracted: {filename}")

    return output_dir


def import_from_directory(data_dir: Path, db_path: Optional[Path] = None) -> dict:
    """Import all CSV files from a local directory into SQLite.

    Args:
        data_dir: Path to directory containing CSV files.
        db_path: Path to SQLite database. Defaults to data/rainfall.db.

    Returns:
        Dictionary with import statistics.
    """
    conn = get_connection(db_path)
    create_tables(conn)

    stats = {
        "stations": 0,
        "rainfall_records": 0,
        "files_processed": 0,
    }

    # Get all CSV files except the national series
    csv_files = sorted(data_dir.glob("*.csv"))

    for csv_path in csv_files:
        # Skip the national series file (different format)
        if "National" in csv_path.name:
            print(f"Skipping national series file: {csv_path.name}")
            continue

        print(f"Processing: {csv_path.name}")

        station, rainfall_records = parse_station_csv(csv_path)

        # Insert station
        station_id = insert_station(
            conn,
            name=station["name"],
            county=station["county"],
            easting=station["easting"],
            northing=station["northing"],
            latitude=station["latitude"],
            longitude=station["longitude"],
            elevation_metres=station["elevation_metres"],
        )
        stats["stations"] += 1

        # Prepare rainfall records with station_id
        records = [
            (station_id, r["year"], r["month"], r["amount_mm"])
            for r in rainfall_records
        ]

        insert_rainfall_batch(conn, records)
        stats["rainfall_records"] += len(records)
        stats["files_processed"] += 1

    conn.close()
    return stats


def handle_interrupt(signum: int, frame) -> None:
    """Handle Ctrl+C gracefully."""
    print("\nImport cancelled by user.")
    sys.exit(1)


def main() -> None:
    """Main entry point for the import script."""
    signal.signal(signal.SIGINT, handle_interrupt)

    parser = argparse.ArgumentParser(
        description="Import Irish rainfall data into SQLite database."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Path to directory containing CSV files. "
             "If omitted, downloads from Met Éireann.",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DATA_URL,
        help=f"URL to download data from (default: Met Éireann)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database (default: data/rainfall.db)",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="Directory to store raw CSV files (default: data/raw/)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-import even if database already exists",
    )

    args = parser.parse_args()

    # Set paths
    db_path = args.db or DEFAULT_DB_PATH
    raw_dir = args.raw_dir or RAW_DATA_DIR

    # Check if database already exists
    if db_path.exists() and not args.force:
        print(f"Database already exists at: {db_path}")
        print("Use --force to re-import data, or delete the database first.")
        sys.exit(0)

    # Remove existing database if force flag is set
    if db_path.exists() and args.force:
        print(f"Removing existing database: {db_path}")
        db_path.unlink()

    # Determine import source
    if args.source is None:
        # Download from URL and extract to raw directory
        print("Downloading Irish rainfall data from Met Éireann...")
        zip_data = download_data(args.url)

        print("\nExtracting raw data files...")
        data_dir = extract_zip_to_directory(zip_data, raw_dir)

        print("\nImporting data into database...")
        stats = import_from_directory(data_dir, db_path)
    else:
        # Import from local directory
        data_dir = Path(args.source)
        if not data_dir.exists():
            print(f"Error: Data directory not found: {data_dir}")
            sys.exit(1)
        print(f"Importing data from: {data_dir}")
        stats = import_from_directory(data_dir, db_path)

    print("\nImport complete!")
    print(f"  Stations imported: {stats['stations']}")
    print(f"  Rainfall records: {stats['rainfall_records']:,}")
    print(f"  Files processed: {stats['files_processed']}")
    print(f"\nDatabase saved to: {db_path}")
    if args.source is None:
        print(f"Raw data saved to: {raw_dir}")


if __name__ == "__main__":
    main()
