"""Change point detection in Irish rainfall data.

Supports two algorithms:
- Apache Otava: E-divisive algorithm optimized for CI/CD performance testing
- ruptures: PELT algorithm designed for climate/signal time series analysis
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import ruptures as rpt
import yaml

from irish_rainfall.database import DEFAULT_DB_PATH, get_connection


def get_annual_rainfall_data(
    db_path: Optional[Path] = None,
    station_id: Optional[int] = None,
) -> tuple[list[int], list[float], str]:
    """Get annual rainfall data from database.

    Args:
        db_path: Path to SQLite database. Defaults to data/rainfall.db.
        station_id: Optional station ID to filter. If None, returns national average.

    Returns:
        Tuple of (years, rainfall_values, station_name).
    """
    conn = get_connection(db_path)

    if station_id:
        # Single station annual totals
        cursor = conn.execute("""
            SELECT
                s.name as station,
                r.year,
                SUM(r.amount_mm) as annual_rainfall
            FROM rainfall r
            JOIN stations s ON r.station_id = s.id
            WHERE r.station_id = ?
            GROUP BY r.year
            ORDER BY r.year
        """, (station_id,))
        rows = cursor.fetchall()
        station_name = rows[0][0] if rows else "Unknown"
    else:
        # National average annual totals
        cursor = conn.execute("""
            SELECT
                'National Average' as station,
                year,
                AVG(annual_total) as annual_rainfall
            FROM (
                SELECT station_id, year, SUM(amount_mm) as annual_total
                FROM rainfall
                GROUP BY station_id, year
            )
            GROUP BY year
            ORDER BY year
        """)
        rows = cursor.fetchall()
        station_name = "National Average"

    conn.close()

    years = [row[1] for row in rows]
    values = [row[2] for row in rows if row[2] is not None]

    return years, values, station_name


def detect_changepoints_ruptures(
    values: list[float],
    years: list[int],
    n_changepoints: Optional[int] = None,
    penalty: float = 10.0,
    model: str = "l2",
) -> list[dict]:
    """Detect change points using ruptures library (PELT algorithm).

    Args:
        values: List of annual rainfall values.
        years: List of corresponding years.
        n_changepoints: Number of change points to detect. If None, auto-detect.
        penalty: Penalty for adding change points (higher = fewer change points).
        model: Cost model ('l1', 'l2', 'rbf', 'linear', 'normal', 'ar').

    Returns:
        List of change point dictionaries with year, index, and statistics.
    """
    signal = np.array(values)

    if n_changepoints is not None:
        # Use Binseg with known number of change points
        algo = rpt.Binseg(model=model, min_size=10).fit(signal)
        result = algo.predict(n_bkps=n_changepoints)
    else:
        # Use Pelt with penalty-based detection
        # Scale penalty by signal variance for better sensitivity control
        sigma = np.std(signal)
        scaled_penalty = penalty * (sigma ** 2)
        algo = rpt.Pelt(model=model, min_size=10).fit(signal)
        result = algo.predict(pen=scaled_penalty)

    # Remove the last element (always equals len(signal))
    changepoints_indices = result[:-1]

    changepoints = []
    for idx in changepoints_indices:
        # Calculate statistics around the change point
        before = signal[max(0, idx-10):idx]
        after = signal[idx:min(len(signal), idx+10)]

        cp = {
            "index": idx,
            "year": years[idx] if idx < len(years) else years[-1],
            "before_mean": float(np.mean(before)) if len(before) > 0 else None,
            "after_mean": float(np.mean(after)) if len(after) > 0 else None,
            "change_mm": float(np.mean(after) - np.mean(before)) if len(before) > 0 and len(after) > 0 else None,
            "algorithm": "ruptures-PELT",
        }

        if cp["before_mean"] and cp["after_mean"]:
            cp["change_percent"] = ((cp["after_mean"] - cp["before_mean"]) / cp["before_mean"]) * 100

        changepoints.append(cp)

    return changepoints


def export_for_otava(
    years: list[int],
    values: list[float],
    station_name: str,
    output_path: Path,
) -> dict:
    """Export data to CSV format for Otava.

    Args:
        years: List of years.
        values: List of rainfall values.
        station_name: Name of the station.
        output_path: Path to write CSV file.

    Returns:
        Dictionary with export statistics.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['time', 'station', 'annual_rainfall'])

        for year, value in zip(years, values):
            writer.writerow([
                f"{year}-01-01 00:00:00 +0000",
                station_name,
                f"{value:.1f}" if value else ""
            ])

    return {'records': len(years), 'output_path': str(output_path)}


def create_otava_config(
    csv_path: Path,
    config_path: Path,
    test_name: str = "rainfall.annual",
) -> Path:
    """Create an Otava configuration file for rainfall analysis.

    Args:
        csv_path: Path to the CSV data file.
        config_path: Path to write configuration file.
        test_name: Name for the test in Otava.

    Returns:
        Path to the configuration file.
    """
    config = {
        'tests': {
            test_name: {
                'type': 'csv',
                'file': str(csv_path.absolute()),
                'time_column': 'time',
                'attributes': ['station'],
                'metrics': ['annual_rainfall'],
            }
        }
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

    return config_path


def run_otava_analysis(
    config_path: Path,
    test_name: str,
    p_value: float = 0.5,
) -> list[dict]:
    """Run Otava change point detection analysis.

    Args:
        config_path: Path to Otava configuration file.
        test_name: Name of the test to analyze.
        p_value: Maximum P-value threshold.

    Returns:
        List of detected change points (may be empty).
    """
    try:
        result = subprocess.run(
            [
                'otava',
                '--config-file', str(config_path),
                'analyze', test_name,
                '--output', 'json',
                '-P', str(p_value),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0 and result.stdout:
            # Parse JSON output
            data = json.loads(result.stdout.split('\n')[-1])
            changepoints = data.get(test_name, [])

            # Convert to standard format
            return [
                {
                    "year": cp.get("time"),
                    "metric": cp.get("metric"),
                    "change_percent": cp.get("magnitude"),
                    "p_value": cp.get("pvalue"),
                    "algorithm": "otava-edivisive",
                }
                for cp in changepoints
            ]

        return []

    except FileNotFoundError:
        print("Warning: 'otava' command not found. Install with: pipx install git+https://github.com/apache/otava.git")
        return []
    except subprocess.TimeoutExpired:
        print("Warning: Otava analysis timed out")
        return []
    except json.JSONDecodeError:
        return []


def analyze_all_stations(
    db_path: Optional[Path] = None,
    penalty: float = 15.0,
) -> dict:
    """Analyze all stations for change points.

    Args:
        db_path: Path to SQLite database.
        penalty: Penalty for ruptures algorithm.

    Returns:
        Dictionary mapping station IDs to their change points.
    """
    conn = get_connection(db_path)

    cursor = conn.execute("SELECT id, name FROM stations ORDER BY name")
    stations = cursor.fetchall()
    conn.close()

    results = {}

    for station_id, station_name in stations:
        years, values, _ = get_annual_rainfall_data(db_path, station_id)

        if len(values) > 20:  # Need enough data for change point detection
            changepoints = detect_changepoints_ruptures(values, years, penalty=penalty)
            results[station_id] = {
                "name": station_name,
                "changepoints": changepoints,
            }

    return results


def main() -> None:
    """Main entry point for change point analysis."""
    parser = argparse.ArgumentParser(
        description="Detect change points in Irish rainfall data."
    )
    parser.add_argument(
        "--station",
        type=int,
        default=None,
        help="Station ID to analyze. If omitted, analyzes national average.",
    )
    parser.add_argument(
        "--all-stations",
        action="store_true",
        help="Analyze all stations.",
    )
    parser.add_argument(
        "--algorithm",
        choices=["ruptures", "otava", "both"],
        default="both",
        help="Algorithm to use (default: both).",
    )
    parser.add_argument(
        "--penalty",
        type=float,
        default=3.0,
        help="Penalty for ruptures PELT algorithm (default: 3.0). Higher = fewer change points.",
    )
    parser.add_argument(
        "--p-value",
        type=float,
        default=0.5,
        help="P-value threshold for Otava (default: 0.5).",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/otava"),
        help="Directory for output files (default: data/otava).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to SQLite database (default: data/rainfall.db).",
    )

    args = parser.parse_args()

    all_results = {}

    if args.all_stations:
        print("Analyzing all stations for change points...")
        print("-" * 60)

        results = analyze_all_stations(args.db, args.penalty)

        for station_id, data in results.items():
            if data["changepoints"]:
                all_results[data["name"]] = data["changepoints"]
                print(f"\n{data['name']}:")
                for cp in data["changepoints"]:
                    change = cp.get("change_mm", 0)
                    direction = "increase" if change > 0 else "decrease"
                    print(f"  {cp['year']}: {abs(change):.1f}mm {direction}")
                    print(f"    Before: {cp['before_mean']:.1f}mm, After: {cp['after_mean']:.1f}mm")
    else:
        # Analyze single station or national average
        years, values, station_name = get_annual_rainfall_data(args.db, args.station)

        print(f"Analyzing: {station_name}")
        print(f"Data range: {years[0]} - {years[-1]} ({len(years)} years)")
        print("-" * 60)

        # Run ruptures analysis
        if args.algorithm in ["ruptures", "both"]:
            print("\n=== Ruptures (PELT Algorithm) ===")
            changepoints = detect_changepoints_ruptures(values, years, penalty=args.penalty)

            if changepoints:
                all_results["ruptures"] = changepoints
                for cp in changepoints:
                    change = cp.get("change_mm", 0)
                    pct = cp.get("change_percent", 0)
                    direction = "increase" if change > 0 else "decrease"
                    print(f"\nChange point at {cp['year']}:")
                    print(f"  Before: {cp['before_mean']:.1f} mm/year average")
                    print(f"  After:  {cp['after_mean']:.1f} mm/year average")
                    print(f"  Change: {abs(change):.1f} mm ({abs(pct):.1f}% {direction})")
            else:
                print("No significant change points detected.")

        # Run Otava analysis
        if args.algorithm in ["otava", "both"]:
            print("\n=== Apache Otava (E-Divisive Algorithm) ===")

            # Export data for Otava
            output_dir = args.output_dir
            csv_path = output_dir / "rainfall_data.csv"
            config_path = output_dir / "otava.yaml"
            test_name = f"rainfall.station_{args.station}" if args.station else "rainfall.national"

            export_for_otava(years, values, station_name, csv_path)
            create_otava_config(csv_path, config_path, test_name)

            changepoints = run_otava_analysis(config_path, test_name, args.p_value)

            if changepoints:
                all_results["otava"] = changepoints
                for cp in changepoints:
                    print(f"\nChange point at {cp['year']}:")
                    print(f"  P-value: {cp.get('p_value', 'N/A')}")
                    print(f"  Magnitude: {cp.get('change_percent', 'N/A')}%")
            else:
                print("No significant change points detected.")
                print("(Otava is optimized for detecting sudden shifts in software metrics,")
                print(" not gradual climate trends with high natural variability.)")

    # Output JSON if requested
    if args.output == "json":
        print("\n" + "=" * 60)
        print("JSON Output:")
        print(json.dumps(all_results, indent=2))


if __name__ == "__main__":
    main()
