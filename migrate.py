"""Schema fix-ups that `Base.metadata.create_all` can't do on an existing DB.

`init_db` only *creates* missing tables (so the new `temperature_readings`
table appears on the next server start on its own); it never ALTERs a table
that already exists, and it never moves data. Run this once against an
existing yard.db after pulling schema changes:

    uv run migrate.py            # operates on yard.db
    uv run migrate.py other.db

It is idempotent.
"""

import sqlite3
import sys

# table -> {column: SQLite column type}
ADDITIONS: dict[str, dict[str, str]] = {
    "events": {
        "ended_at": "DATETIME",
        "cross_stuff_target": "VARCHAR",
        "new_container_number": "VARCHAR(11)",
        "original_emptied": "BOOLEAN",
    },
}


def _add_columns(con: sqlite3.Connection) -> None:
    for table, cols in ADDITIONS.items():
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if not existing:
            print(f"{table}: no such table yet — skipped (created fresh on startup)")
            continue
        for name, decl in cols.items():
            if name in existing:
                continue
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            print(f"{table}: added {name} {decl}")


def _split_pti_plug_kinds(con: sqlite3.Connection) -> None:
    """PTI plugs used to share `plug_in`/`plug_out` with storage plugs, telling
    them apart only by `purpose`. They now have their own EventKind so they can
    be counted separately. Reclassify existing rows; idempotent.
    """
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "events" not in tables:
        return
    ins = con.execute(
        "UPDATE events SET kind = 'pti_plug_in' WHERE kind = 'plug_in' AND purpose = 'PTI'"
    ).rowcount
    # a PTI unplug is a plug_out that carries a sticker (storage unplugs never do)
    outs = con.execute(
        "UPDATE events SET kind = 'pti_plug_out' "
        "WHERE kind = 'plug_out' AND (purpose = 'PTI' OR sticker IS NOT NULL)"
    ).rowcount
    if ins or outs:
        print(f"events: reclassified {ins} plug-in and {outs} plug-out row(s) as PTI")


def _move_temperature_out_of_events(con: sqlite3.Connection) -> None:
    """Temperature rounds moved from the event log into their own table."""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "events" not in tables or "temperature_readings" not in tables:
        return
    # A DB created after the temperature-table split has no time_slot column on
    # events, so there is nothing to move.
    if "time_slot" not in {row[1] for row in con.execute("PRAGMA table_info(events)")}:
        return
    rows = con.execute(
        """SELECT id, container_number, at, created_at, voided_at, time_slot,
                  set_point_c, supply_temp_c, return_temp_c, temperature_remark, comments
           FROM events WHERE kind = 'temperature'"""
    ).fetchall()
    if not rows:
        return
    con.executemany(
        """INSERT INTO temperature_readings
             (container_number, at, created_at, voided_at, time_slot,
              set_point_c, supply_temp_c, return_temp_c, temperature_remark, comments)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [r[1:] for r in rows],
    )
    con.execute("DELETE FROM events WHERE kind = 'temperature'")
    print(f"events: moved {len(rows)} temperature row(s) into temperature_readings")


def main(db: str = "yard.db") -> None:
    con = sqlite3.connect(db)
    try:
        _add_columns(con)
        _split_pti_plug_kinds(con)
        _move_temperature_out_of_events(con)
        con.commit()
    finally:
        con.close()
    print("done")


if __name__ == "__main__":
    main(*sys.argv[1:])
