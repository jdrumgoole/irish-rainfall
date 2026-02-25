"""
Analyze the longest period of consecutive rainfall in Valentia.

Uses monthly rainfall data from the IIP dataset (1850-2010) to find
the longest unbroken streak of months with recorded rainfall (> 0 mm).
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "rainfall.db")


def analyze_consecutive_rainfall(station_name="Valentia"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM stations WHERE name LIKE ?", (f"%{station_name}%",))
    station = cur.fetchone()
    if not station:
        print(f"Station '{station_name}' not found")
        return

    station_id, name = station
    print(f"Station: {name} (id={station_id})")

    cur.execute(
        """
        SELECT year, month, amount_mm
        FROM rainfall
        WHERE station_id = ?
        ORDER BY year, month
        """,
        (station_id,),
    )
    rows = cur.fetchall()
    print(f"Total monthly records: {len(rows)}")

    # Find all consecutive streaks of non-zero rainfall
    streaks = []
    current_streak = 0
    current_start = None

    for year, month, amount in rows:
        if amount is not None and amount > 0:
            if current_streak == 0:
                current_start = (year, month)
            current_streak += 1
        else:
            if current_streak > 0:
                prev_idx = rows.index((year, month, amount)) - 1
                prev = rows[prev_idx]
                streaks.append((current_streak, current_start, (prev[0], prev[1])))
            current_streak = 0

    if current_streak > 0:
        streaks.append((current_streak, current_start, (rows[-1][0], rows[-1][1])))

    streaks.sort(key=lambda x: x[0], reverse=True)

    print("\nConsecutive rainfall streaks (months with > 0 mm):")
    print("=" * 65)
    for i, (length, start, end) in enumerate(streaks):
        years = length // 12
        months = length % 12
        print(
            f"  {i + 1}. {length} months ({years} yrs {months} mo): "
            f"{start[0]}-{start[1]:02d} to {end[0]}-{end[1]:02d}"
        )

    print("\nMonths with ZERO rainfall:")
    print("-" * 40)
    for y, m, a in rows:
        if a is None or a == 0:
            idx = rows.index((y, m, a))
            before = rows[idx - 1] if idx > 0 else None
            after = rows[idx + 1] if idx < len(rows) - 1 else None
            print(f"  {y}-{m:02d}: {a} mm")
            if before:
                print(f"    Month before: {before[0]}-{before[1]:02d}: {before[2]} mm")
            if after:
                print(f"    Month after:  {after[0]}-{after[1]:02d}: {after[2]} mm")

    conn.close()


if __name__ == "__main__":
    analyze_consecutive_rainfall()
