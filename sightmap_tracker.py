#!/usr/bin/env python3
"""
Fetch AVE Santa Clara (SightMap) availability, store snapshots in SQLite,
diff against the previous run, log changes, and optionally notify Discord.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

DEFAULT_SIGHTMAP_URL = (
    "https://sightmap.com/app/api/v1/jlw075ogv2y/sightmaps/83961"
)
DISCORD_CONTENT_LIMIT = 1900

# -----------------------------------------------------------------------------
# Data
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitRow:
    sightmap_unit_id: str
    unit_number: str
    display_unit_number: str
    floor_plan: str
    floor_label: str
    price: int | None
    available_on: str | None
    display_price: str
    display_available_on: str
    area: int | None = None
    specials_description: str | None = None
    bed_bath_label: str = ""

    def label(self) -> str:
        base = f"{self.display_unit_number or self.unit_number} | {self.floor_plan} | {self.floor_label}"
        bb = (self.bed_bath_label or "").strip()
        if bb and bb != "—":
            return f"{base} ({bb})"
        return base


@dataclass
class DiffResult:
    new_units: list[UnitRow]
    removed_units: list[UnitRow]
    price_changes: list[tuple[UnitRow, UnitRow]]
    available_changes: list[tuple[UnitRow, UnitRow]]
    specials_changes: list[tuple[UnitRow, UnitRow]]

    def any(self) -> bool:
        return bool(
            self.new_units
            or self.removed_units
            or self.price_changes
            or self.available_changes
            or self.specials_changes
        )


# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------


def fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "sightmap-tracker/1.0 (personal availability monitor)",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        raw = resp.read()
    if len(raw) >= 2 and raw[0:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def post_discord_webhook(webhook_url: str, content: str) -> None:
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "sightmap-tracker/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord HTTP {resp.status}")


def post_discord_chunks(webhook_url: str, text: str, max_len: int = DISCORD_CONTENT_LIMIT) -> None:
    """Send long text as multiple Discord messages (rate-limit friendly)."""
    text = text.strip()
    if not text:
        return
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            chunks.append(rest)
            break
        chunk = rest[:max_len]
        nl = chunk.rfind("\n")
        if nl > max_len // 2:
            chunk = rest[: nl + 1]
        chunks.append(chunk.rstrip("\n"))
        rest = rest[len(chunk) :].lstrip("\n")
    n = len(chunks)
    for i, ch in enumerate(chunks, start=1):
        body = f"({i}/{n})\n{ch}" if n > 1 else ch
        post_discord_webhook(webhook_url, body)
        if i < n:
            time.sleep(0.7)


# -----------------------------------------------------------------------------
# Parse
# -----------------------------------------------------------------------------


def _norm_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _short_text(text: str | None, max_len: int = 160) -> str:
    s = " ".join((text or "").split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _norm_special_text(s: str | None) -> str:
    """Normalize specials_description for comparisons."""
    return (s or "").strip()


def parse_units(payload: dict[str, Any]) -> tuple[str, list[UnitRow]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("JSON missing data object")

    asset = data.get("asset") or {}
    asset_name = str(asset.get("name") or "SightMap")

    floor_plans = data.get("floor_plans") or []
    plan_names: dict[str, str] = {}
    plan_bed_bath: dict[str, str] = {}
    for fp in floor_plans:
        if not isinstance(fp, dict):
            continue
        pid = fp.get("id")
        if pid is None:
            continue
        pid_s = str(pid)
        name = fp.get("name") or fp.get("filter_label") or str(pid)
        plan_names[pid_s] = str(name)
        bed = str(fp.get("bedroom_label") or "").strip()
        bath = str(fp.get("bathroom_label") or "").strip()
        if bed and bath:
            plan_bed_bath[pid_s] = f"{bed} / {bath}"
        else:
            plan_bed_bath[pid_s] = bed or bath or "—"

    floors = data.get("floors") or []
    floor_labels: dict[str, str] = {}
    for fl in floors:
        if not isinstance(fl, dict):
            continue
        fid = fl.get("id")
        if fid is None:
            continue
        label = fl.get("filter_label") or fl.get("filter_short_label") or str(fid)
        floor_labels[str(fid)] = str(label)

    units_raw = data.get("units")
    if not isinstance(units_raw, list):
        raise ValueError("JSON missing data.units array")

    rows: list[UnitRow] = []
    for u in units_raw:
        if not isinstance(u, dict):
            continue
        uid = u.get("id")
        if uid is None:
            continue
        fp_id = str(u.get("floor_plan_id") or "")
        fl_id = str(u.get("floor_id") or "")
        price = u.get("price")
        price_int: int | None
        if price is None or price == "":
            price_int = None
        else:
            try:
                price_int = int(price)
            except (TypeError, ValueError):
                price_int = None

        raw_area = u.get("area")
        if raw_area is None or raw_area == "":
            area_int = None
        else:
            try:
                area_int = int(raw_area)
            except (TypeError, ValueError):
                area_int = None

        rows.append(
            UnitRow(
                sightmap_unit_id=str(uid),
                unit_number=str(u.get("unit_number") or u.get("label") or ""),
                display_unit_number=str(
                    u.get("display_unit_number") or u.get("label") or ""
                ),
                floor_plan=plan_names.get(fp_id, fp_id or "?"),
                floor_label=floor_labels.get(fl_id, fl_id or "?"),
                price=price_int,
                available_on=_norm_str(u.get("available_on")),
                display_price=str(u.get("display_price") or ""),
                display_available_on=str(u.get("display_available_on") or ""),
                area=area_int,
                specials_description=_norm_str(u.get("specials_description")),
                bed_bath_label=plan_bed_bath.get(fp_id, "—"),
            )
        )

    return asset_name, rows


# -----------------------------------------------------------------------------
# SQLite
# -----------------------------------------------------------------------------


def db_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL,
            unit_count INTEGER NOT NULL,
            asset_name TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS snapshot_units (
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
            sightmap_unit_id TEXT NOT NULL,
            unit_number TEXT NOT NULL,
            display_unit_number TEXT NOT NULL,
            floor_plan TEXT NOT NULL,
            floor_label TEXT NOT NULL,
            price INTEGER,
            available_on TEXT,
            display_price TEXT NOT NULL,
            display_available_on TEXT NOT NULL,
            area INTEGER,
            specials_description TEXT,
            bed_bath_label TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (snapshot_id, sightmap_unit_id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshot_units_snapshot
            ON snapshot_units(snapshot_id);
        """
    )
    conn.commit()
    _migrate_schema(conn)


def _migrate_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshot_units)").fetchall()}
    if "area" not in cols:
        conn.execute("ALTER TABLE snapshot_units ADD COLUMN area INTEGER")
        conn.commit()
    if "specials_description" not in cols:
        conn.execute("ALTER TABLE snapshot_units ADD COLUMN specials_description TEXT")
        conn.commit()
    if "bed_bath_label" not in cols:
        conn.execute(
            "ALTER TABLE snapshot_units ADD COLUMN bed_bath_label TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()


def save_snapshot(conn: sqlite3.Connection, asset_name: str, units: list[UnitRow]) -> int:
    fetched_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO snapshots (fetched_at, unit_count, asset_name) VALUES (?, ?, ?)",
        (fetched_at, len(units), asset_name),
    )
    sid = int(cur.lastrowid)
    conn.executemany(
        """
        INSERT INTO snapshot_units (
            snapshot_id, sightmap_unit_id, unit_number, display_unit_number,
            floor_plan, floor_label, price, available_on,
            display_price, display_available_on, area, specials_description,
            bed_bath_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                sid,
                u.sightmap_unit_id,
                u.unit_number,
                u.display_unit_number,
                u.floor_plan,
                u.floor_label,
                u.price,
                u.available_on,
                u.display_price,
                u.display_available_on,
                u.area,
                u.specials_description,
                u.bed_bath_label or "",
            )
            for u in units
        ],
    )
    conn.commit()
    return sid


def row_to_unit(r: sqlite3.Row) -> UnitRow:
    keys = r.keys()
    area_val: int | None
    if "area" in keys and r["area"] is not None:
        try:
            area_val = int(r["area"])
        except (TypeError, ValueError):
            area_val = None
    else:
        area_val = None
    spec_val: str | None
    if "specials_description" in keys and r["specials_description"] is not None:
        ss = str(r["specials_description"]).strip()
        spec_val = ss if ss else None
    else:
        spec_val = None
    bb_val = ""
    if "bed_bath_label" in keys and r["bed_bath_label"] is not None:
        bb_val = str(r["bed_bath_label"]).strip()
    return UnitRow(
        sightmap_unit_id=str(r["sightmap_unit_id"]),
        unit_number=str(r["unit_number"]),
        display_unit_number=str(r["display_unit_number"]),
        floor_plan=str(r["floor_plan"]),
        floor_label=str(r["floor_label"]),
        price=r["price"] if r["price"] is not None else None,
        available_on=r["available_on"],
        display_price=str(r["display_price"]),
        display_available_on=str(r["display_available_on"]),
        area=area_val,
        specials_description=spec_val,
        bed_bath_label=bb_val or "—",
    )


def load_snapshot_units(conn: sqlite3.Connection, snapshot_id: int) -> dict[str, UnitRow]:
    cur = conn.execute(
        "SELECT * FROM snapshot_units WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    return {str(r["sightmap_unit_id"]): row_to_unit(r) for r in cur.fetchall()}


def get_last_two_snapshot_ids(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    cur = conn.execute(
        "SELECT id, asset_name FROM snapshots ORDER BY id DESC LIMIT 2"
    )
    return [(int(r["id"]), str(r["asset_name"])) for r in cur.fetchall()]


# -----------------------------------------------------------------------------
# Diff
# -----------------------------------------------------------------------------


def diff_snapshots(prev: dict[str, UnitRow], curr: dict[str, UnitRow]) -> DiffResult:
    prev_ids = set(prev)
    curr_ids = set(curr)

    new_units = [curr[i] for i in sorted(curr_ids - prev_ids, key=lambda x: curr[x].unit_number)]
    removed_units = [prev[i] for i in sorted(prev_ids - curr_ids, key=lambda x: prev[x].unit_number)]

    price_changes: list[tuple[UnitRow, UnitRow]] = []
    available_changes: list[tuple[UnitRow, UnitRow]] = []
    specials_changes: list[tuple[UnitRow, UnitRow]] = []

    for uid in prev_ids & curr_ids:
        a, b = prev[uid], curr[uid]
        if a.price != b.price:
            price_changes.append((a, b))
        if (a.available_on or "") != (b.available_on or ""):
            available_changes.append((a, b))
        if _norm_special_text(a.specials_description) != _norm_special_text(
            b.specials_description
        ):
            specials_changes.append((a, b))

    price_changes.sort(key=lambda t: t[0].unit_number)
    available_changes.sort(key=lambda t: t[0].unit_number)
    specials_changes.sort(key=lambda t: t[0].unit_number)

    return DiffResult(
        new_units=new_units,
        removed_units=removed_units,
        price_changes=price_changes,
        available_changes=available_changes,
        specials_changes=specials_changes,
    )


# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------


def floor_rank_label(label: str) -> int:
    m = re.search(r"(\d+)", label or "")
    return int(m.group(1)) if m else 9999


def unit_rank_tuple(num: str) -> tuple[int, str]:
    s = (num or "").strip()
    return (int(s), s) if s.isdigit() else (99999, s)


def sort_units_by_floor_then_number(units: list[UnitRow]) -> list[UnitRow]:
    """Higher floor first; within a floor, lower unit # first."""
    return sorted(
        units,
        key=lambda u: (
            -floor_rank_label(u.floor_label),
            unit_rank_tuple(u.unit_number),
        ),
    )


_DIFF_KIND_ORDER = {"REMOVED": 0, "NEW": 1, "PRICE": 2, "AVAILABLE": 3, "SPECIAL": 4}


def build_diff_events(diff: DiffResult) -> list[dict[str, object]]:
    """Single timeline: high floor first, then unit #, then event kind."""
    rows: list[dict[str, object]] = []

    for u in diff.removed_units:
        fr = floor_rank_label(u.floor_label)
        ur, uk = unit_rank_tuple(u.unit_number)
        rent = u.display_price or (f"${u.price}" if u.price is not None else "—")
        sp = _norm_special_text(u.specials_description)
        spx = f" · had special: {_short_text(sp, 90)}" if sp else ""
        rows.append(
            {
                "kind": "REMOVED",
                "summary": f"{u.label()} — no longer listed ({rent}){spx}",
                "floor_rank": fr,
                "unit_sort": ur,
                "unit_key": uk,
            }
        )

    for u in diff.new_units:
        fr = floor_rank_label(u.floor_label)
        ur, uk = unit_rank_tuple(u.unit_number)
        rent = u.display_price or (f"${u.price}" if u.price is not None else "—")
        move = u.display_available_on or u.available_on or "—"
        sp = _norm_special_text(u.specials_description)
        spx = f" · special: {_short_text(sp, 90)}" if sp else ""
        rows.append(
            {
                "kind": "NEW",
                "summary": f"{u.label()} — newly listed ({rent}; {move}){spx}",
                "floor_rank": fr,
                "unit_sort": ur,
                "unit_key": uk,
            }
        )

    for old, new in diff.price_changes:
        fr = floor_rank_label(new.floor_label)
        ur, uk = unit_rank_tuple(new.unit_number)
        op = old.display_price or (f"${old.price}" if old.price is not None else "?")
        np = new.display_price or (f"${new.price}" if new.price is not None else "?")
        rows.append(
            {
                "kind": "PRICE",
                "summary": f"{new.label()} — rent {op} → {np}",
                "floor_rank": fr,
                "unit_sort": ur,
                "unit_key": uk,
            }
        )

    for old, new in diff.available_changes:
        fr = floor_rank_label(new.floor_label)
        ur, uk = unit_rank_tuple(new.unit_number)
        oa = old.display_available_on or old.available_on or "n/a"
        na = new.display_available_on or new.available_on or "n/a"
        rows.append(
            {
                "kind": "AVAILABLE",
                "summary": f"{new.label()} — move-in / availability {oa} → {na}",
                "floor_rank": fr,
                "unit_sort": ur,
                "unit_key": uk,
            }
        )

    for old, new in diff.specials_changes:
        fr = floor_rank_label(new.floor_label)
        ur, uk = unit_rank_tuple(new.unit_number)
        otxt = _norm_special_text(old.specials_description) or "—"
        ntxt = _norm_special_text(new.specials_description) or "—"
        rows.append(
            {
                "kind": "SPECIAL",
                "summary": (
                    f"{new.label()} — special text "
                    f"{_short_text(otxt, 100)} → {_short_text(ntxt, 100)}"
                ),
                "floor_rank": fr,
                "unit_sort": ur,
                "unit_key": uk,
            }
        )

    rows.sort(
        key=lambda e: (
            -int(e["floor_rank"]),
            int(e["unit_sort"]),
            str(e["unit_key"]),
            _DIFF_KIND_ORDER[str(e["kind"])],
        )
    )
    return rows


def format_inventory_table(units_sorted: list[UnitRow]) -> str:
    lines = [
        "Current availability (high floor → low, then unit #)",
        "-" * 132,
        f"{'Floor':<11} {'Unit':<13} {'Plan':<5} {'Layout':<22} {'Sq':>6} {'Rent':>12}  {'Avail':<12}  Specials",
        "-" * 132,
    ]
    for u in units_sorted:
        apt = u.display_unit_number or u.unit_number or "—"
        sq = f"{u.area:,}" if u.area is not None else "—"
        rent = u.display_price or (f"${u.price:,}" if u.price is not None else "—")
        move = (u.display_available_on or u.available_on or "—")[:12]
        sp = _norm_special_text(u.specials_description)
        spc = _short_text(sp, 36) if sp else "—"
        layout = (u.bed_bath_label or "—").strip() or "—"
        if len(layout) > 22:
            layout = layout[:21] + "…"
        lines.append(
            f"{u.floor_label:<11} {apt:<13} {u.floor_plan:<5} {layout:<22} {sq:>6} {rent:>12}  {move:<12}  {spc}"
        )
    lines.append("-" * 132)
    lines.append(f"Total listed: {len(units_sorted)} unit(s)\n")
    return "\n".join(lines)


def format_diff_section(prev_id: int, curr_id: int, diff: DiffResult) -> str:
    lines: list[str] = [
        f"Changes since last run (snapshot #{prev_id} → #{curr_id})",
        "Order: high floor first, then unit #, then event type (incl. SPECIAL).",
        "",
    ]
    if not diff.any():
        lines.append("No changes detected.")
        return "\n".join(lines) + "\n"

    for e in build_diff_events(diff):
        lines.append(f"  [{e['kind']}] {e['summary']}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_run_header(asset_name: str, snapshot_id: int, run_ts: str, unit_count: int) -> str:
    return (
        f"{asset_name}\n"
        f"Run: {run_ts}  |  Snapshot #{snapshot_id}  |  {unit_count} unit(s) in feed\n"
        f"{'=' * 72}\n"
    )


def compose_baseline_report(
    asset_name: str,
    snapshot_id: int,
    run_ts: str,
    units_sorted: list[UnitRow],
) -> str:
    head = format_run_header(asset_name, snapshot_id, run_ts, len(units_sorted))
    inv = format_inventory_table(units_sorted)
    tail = (
        "\n--- Changes ---\n\n"
        "No previous snapshot — this run is the baseline. "
        "Next run will compare against this snapshot.\n"
    )
    return head + "\n" + inv + tail


def compose_compare_report(
    asset_name: str,
    snapshot_id: int,
    run_ts: str,
    units_sorted: list[UnitRow],
    prev_id: int,
    curr_id: int,
    diff: DiffResult,
) -> str:
    head = format_run_header(asset_name, snapshot_id, run_ts, len(units_sorted))
    inv = format_inventory_table(units_sorted)
    changes = "\n--- Changes ---\n\n" + format_diff_section(prev_id, curr_id, diff)
    return head + "\n" + inv + changes


def append_log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    if load_dotenv:
        load_dotenv()

    base = Path(__file__).resolve().parent
    os.chdir(base)

    sightmap_url = os.environ.get("SIGHTMAP_URL", DEFAULT_SIGHTMAP_URL)
    db_path = Path(os.environ.get("DB_PATH", "sightmap.db"))
    log_path = Path(os.environ.get("LOG_PATH", "changes.log"))
    webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    discord_only_on_diff = (os.environ.get("DISCORD_ONLY_ON_CHANGES") or "").lower() in (
        "1",
        "true",
        "yes",
    )

    try:
        payload = fetch_json(sightmap_url)
    except urllib.error.HTTPError as e:
        print(f"HTTP error fetching SightMap: {e.code} {e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Network error fetching SightMap: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Invalid JSON from SightMap: {e}", file=sys.stderr)
        return 1

    try:
        asset_name, units = parse_units(payload)
    except ValueError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1

    conn = db_connect(db_path)
    try:
        init_db(conn)
        snapshot_id = save_snapshot(conn, asset_name, units)
        history = get_last_two_snapshot_ids(conn)

        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_header = f"\n{'=' * 60}\n{run_ts} | snapshot #{snapshot_id} | {len(units)} units\n"
        sorted_units = sort_units_by_floor_then_number(units)

        if len(history) < 2:
            report = compose_baseline_report(
                asset_name, snapshot_id, run_ts, sorted_units
            )
            print(report, end="")
            append_log(log_path, log_header + report + "\n")

            if webhook:
                try:
                    post_discord_chunks(webhook, report)
                except Exception as e:
                    print(f"Discord notify failed: {e}", file=sys.stderr)
                    append_log(log_path, f"Discord notify failed: {e}\n")
            return 0

        curr_id, _ = history[0]
        prev_id, _ = history[1]
        prev_units = load_snapshot_units(conn, prev_id)
        curr_units = load_snapshot_units(conn, curr_id)
        d = diff_snapshots(prev_units, curr_units)
        report = compose_compare_report(
            asset_name, snapshot_id, run_ts, sorted_units, prev_id, curr_id, d
        )

        print(report, end="")
        append_log(log_path, log_header + report + "\n")

        if webhook:
            try:
                if discord_only_on_diff and not d.any():
                    pass
                else:
                    post_discord_chunks(webhook, report)
            except Exception as e:
                print(f"Discord notify failed: {e}", file=sys.stderr)
                append_log(log_path, f"Discord notify failed: {e}\n")
        elif not webhook:
            print(
                "(Set DISCORD_WEBHOOK_URL to post this report to Discord.)",
                file=sys.stderr,
            )

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
