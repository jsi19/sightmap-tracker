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


def _snapshot_history(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT id, fetched_at, asset_name FROM snapshots ORDER BY id ASC"
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "fetched_at": str(row["fetched_at"]),
            "asset_name": str(row["asset_name"]),
        }
        for row in rows
    ]


def _empty_counts() -> dict[str, int]:
    return {"new": 0, "removed": 0, "price": 0, "available": 0, "special": 0}


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
        "specials_description": u.specials_description,
        "bed_bath_label": u.bed_bath_label or "—",
        "label": u.label(),
    }


def build_payload(conn: sqlite3.Connection) -> dict:
    history = _snapshot_history(conn)
    if not history:
        raise SystemExit("No snapshots in database — run sightmap_tracker.py first.")

    current_snapshot = history[-1]
    curr_id = int(current_snapshot["id"])
    asset_name = str(current_snapshot["asset_name"])
    fetched_at = str(current_snapshot["fetched_at"])

    curr_units = st.load_snapshot_units(conn, curr_id)
    sorted_units = st.sort_units_by_floor_then_number(list(curr_units.values()))

    if len(history) < 2:
        changes: dict = {
            "compare": None,
            "has_changes": False,
            "baseline": True,
            "counts": _empty_counts(),
            "events": [],
        }
    else:
        counts = _empty_counts()
        event_groups: list[list[dict[str, object]]] = []

        prev_snapshot = history[0]
        prev_units = st.load_snapshot_units(conn, int(prev_snapshot["id"]))

        for next_snapshot in history[1:]:
            next_id = int(next_snapshot["id"])
            next_units = st.load_snapshot_units(conn, next_id)
            d = st.diff_snapshots(prev_units, next_units)

            counts["new"] += len(d.new_units)
            counts["removed"] += len(d.removed_units)
            counts["price"] += len(d.price_changes)
            counts["available"] += len(d.available_changes)
            counts["special"] += len(d.specials_changes)

            timeline = st.build_diff_events(d)
            if timeline:
                event_groups.append(
                    [
                        {
                            "type": str(e["kind"]),
                            "summary": str(e["summary"]),
                            "from_snapshot_id": int(prev_snapshot["id"]),
                            "to_snapshot_id": next_id,
                            "from_snapshot_fetched_at": str(prev_snapshot["fetched_at"]),
                            "to_snapshot_fetched_at": str(next_snapshot["fetched_at"]),
                            "snapshot_fetched_at": str(next_snapshot["fetched_at"]),
                        }
                        for e in timeline
                    ]
                )

            prev_snapshot = next_snapshot
            prev_units = next_units

        events = [
            event
            for group in reversed(event_groups)
            for event in group
        ]
        changes = {
            "compare": {
                "from_snapshot_id": int(history[0]["id"]),
                "to_snapshot_id": curr_id,
            },
            "has_changes": bool(events),
            "baseline": False,
            "counts": counts,
            "events": events,
            "snapshot_count": len(history),
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
