#!/usr/bin/env python3
"""Build _site/ for GitHub Pages: copy dashboard/ + write snapshot.json from sightmap.db."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import sightmap_tracker as st


def _unit_dict(u: st.UnitRow) -> dict:
    return {
        "sightmap_unit_id": u.sightmap_unit_id,
        "floor_label": u.floor_label,
        "unit_number": u.unit_number,
        "display_unit_number": u.display_unit_number,
        "floor_plan": u.floor_plan,
        "area": u.area,
        "price": u.price,
        "display_price": u.display_price,
        "available_on": u.available_on,
        "display_available_on": u.display_available_on,
        "label": u.label(),
    }


def build_payload(conn: sqlite3.Connection) -> dict:
    history = st.get_last_two_snapshot_ids(conn)
    if not history:
        raise SystemExit("No snapshots in database — run sightmap_tracker.py first.")

    curr_id, asset_name = history[0]
    row = conn.execute(
        "SELECT fetched_at FROM snapshots WHERE id = ?",
        (curr_id,),
    ).fetchone()
    fetched_at = str(row["fetched_at"]) if row else ""

    curr_units = st.load_snapshot_units(conn, curr_id)
    sorted_units = st.sort_units_by_floor_then_number(list(curr_units.values()))

    if len(history) < 2:
        changes: dict = {
            "compare": None,
            "has_changes": False,
            "baseline": True,
            "counts": {"new": 0, "removed": 0, "price": 0, "available": 0},
            "events": [],
        }
    else:
        prev_id, _ = history[1]
        prev_units = st.load_snapshot_units(conn, prev_id)
        d = st.diff_snapshots(prev_units, curr_units)
        timeline = st.build_diff_events(d)
        changes = {
            "compare": {"from_snapshot_id": prev_id, "to_snapshot_id": curr_id},
            "has_changes": d.any(),
            "baseline": False,
            "counts": {
                "new": len(d.new_units),
                "removed": len(d.removed_units),
                "price": len(d.price_changes),
                "available": len(d.available_changes),
            },
            "events": [
                {"type": str(e["kind"]), "summary": str(e["summary"])}
                for e in timeline
            ],
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": curr_id,
        "snapshot_fetched_at": fetched_at,
        "asset_name": asset_name,
        "unit_count": len(sorted_units),
        "units": [_unit_dict(u) for u in sorted_units],
        "changes": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("_site"),
        help="Directory to write site (default: _site)",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    os.chdir(base)
    db_path = Path(os.environ.get("DB_PATH", "sightmap.db"))
    dashboard_src = base / "dashboard"
    out_dir = args.output_dir.resolve()

    if not db_path.is_file():
        print(f"No database at {db_path}", file=sys.stderr)
        return 1
    if not dashboard_src.is_dir():
        print(f"No dashboard/ at {dashboard_src}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in os.listdir(dashboard_src):
        src = dashboard_src / name
        dst = out_dir / name
        if src.is_file():
            shutil.copy2(src, dst)

    conn = st.db_connect(db_path)
    try:
        payload = build_payload(conn)
    finally:
        conn.close()

    (out_dir / "snapshot.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_dir / 'snapshot.json'} ({payload['unit_count']} units)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
