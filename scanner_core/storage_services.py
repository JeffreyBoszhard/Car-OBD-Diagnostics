"""SQLite storage for every persisted record the scanner keeps.

One local database file (scanner_config.db, next to app.py) holds settings,
saved scans, the car journal (garage notes, trips, fuel-ups) and vehicle
profiles. There is no server component and no second backend to configure:
the file can be copied, backed up or deleted on its own.

Every public function here takes the database path as its first argument and
opens a short-lived connection, so the module is safe to call from the Flask
request threads and from the background polling threads at the same time.
"""
import json
import sqlite3
import threading
from pathlib import Path

# SQLite serialises writers itself, but on Windows a second writer arriving
# mid-transaction still raises "database is locked" before the busy timeout
# kicks in often enough to be annoying. One process-wide write lock removes
# that class of error entirely; reads are cheap enough not to need it.
_write_lock = threading.Lock()

BUSY_TIMEOUT_SECONDS = 10.0


def db_path_from_file(app_file):
    return Path(app_file).with_name("scanner_config.db")


def connect(db_path):
    """Open a connection with the pragmas this app wants everywhere."""
    db = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _read(db_path, sql, params=()):
    with connect(db_path) as db:
        return db.execute(sql, params).fetchall()


def _read_one(db_path, sql, params=()):
    with connect(db_path) as db:
        return db.execute(sql, params).fetchone()


def _write(db_path, sql, params=()):
    """Run a single INSERT/UPDATE/DELETE and return (rowcount, lastrowid)."""
    with _write_lock, connect(db_path) as db:
        cursor = db.execute(sql, params)
        db.commit()
        return cursor.rowcount, cursor.lastrowid


def _table_columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def init_storage(db_path):
    with _write_lock, connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                label TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS garage_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                vin TEXT NOT NULL DEFAULT '',
                plate TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                mileage TEXT NOT NULL,
                note TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS fuel_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                mileage_km REAL NOT NULL,
                liters REAL NOT NULL,
                cost REAL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                nickname TEXT NOT NULL,
                vin TEXT NOT NULL DEFAULT '',
                plate TEXT NOT NULL DEFAULT '',
                brand TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT ''
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                start_mileage_km REAL NOT NULL,
                end_mileage_km REAL NOT NULL,
                purpose TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                vehicle_id INTEGER
            )
            """
        )

        # --- forward-only migrations -------------------------------------
        if "share_token" not in _table_columns(db, "scans"):
            db.execute("ALTER TABLE scans ADD COLUMN share_token TEXT")

        note_columns = _table_columns(db, "garage_notes")
        if "vin" not in note_columns:
            db.execute("ALTER TABLE garage_notes ADD COLUMN vin TEXT NOT NULL DEFAULT ''")
        if "plate" not in note_columns:
            db.execute("ALTER TABLE garage_notes ADD COLUMN plate TEXT NOT NULL DEFAULT ''")
        # Photo attachments used to live inside payload_json as a base64 data
        # URI. They are files on disk now, referenced by name.
        if "attachment_name" not in note_columns:
            db.execute("ALTER TABLE garage_notes ADD COLUMN attachment_name TEXT")

        vehicle_columns = _table_columns(db, "vehicles")
        for column in ("brand", "model", "year"):
            if column not in vehicle_columns:
                db.execute(f"ALTER TABLE vehicles ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "tank_liters" not in vehicle_columns:
            db.execute("ALTER TABLE vehicles ADD COLUMN tank_liters REAL")

        # Multi-vehicle tagging is opt-in and forward-only: existing scans,
        # garage notes and fuel logs are left unassigned (vehicle_id NULL)
        # rather than being retroactively guessed at by VIN/plate matching.
        for table in ("scans", "garage_notes", "fuel_logs"):
            if "vehicle_id" not in _table_columns(db, table):
                db.execute(f"ALTER TABLE {table} ADD COLUMN vehicle_id INTEGER")

        # --- indexes ------------------------------------------------------
        db.execute("CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(id DESC)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_scans_share ON scans(share_token) WHERE share_token IS NOT NULL")
        db.execute("CREATE INDEX IF NOT EXISTS idx_notes_vehicle ON garage_notes(vehicle_id, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_fuel_vehicle ON fuel_logs(vehicle_id, mileage_km DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, id DESC)")
        db.commit()


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def get_setting(db_path, key, default=None):
    row = _read_one(db_path, "SELECT value FROM settings WHERE key = ?", (key,))
    return row[0] if row else default


def get_all_settings(db_path):
    """Read the whole settings table in one query, for the settings cache."""
    return {row["key"]: row["value"] for row in _read(db_path, "SELECT key, value FROM settings")}


def set_setting(db_path, key, value):
    _write(
        db_path,
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    return True


# ---------------------------------------------------------------------------
# saved scans
# ---------------------------------------------------------------------------
def save_scan_snapshot(db_path, created_at, label, summary, payload, vehicle_id=None):
    _, scan_id = _write(
        db_path,
        """
        INSERT INTO scans (created_at, label, summary, payload_json, vehicle_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (created_at, label, summary, json.dumps(payload), vehicle_id),
    )
    return {
        "id": scan_id,
        "created_at": created_at,
        "label": label,
        "summary": summary,
    }


def get_recent_scans(db_path, limit):
    rows = _read(
        db_path,
        """
        SELECT id, created_at, label, summary, payload_json, share_token
        FROM scans
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "label": row["label"],
            "summary": row["summary"],
            "payload": json.loads(row["payload_json"]),
            "share_token": row["share_token"],
        }
        for row in rows
    ]


def delete_scan_snapshot(db_path, scan_id):
    rowcount, _ = _write(db_path, "DELETE FROM scans WHERE id = ?", (scan_id,))
    return rowcount > 0


def set_scan_share_token(db_path, scan_id, token):
    rowcount, _ = _write(db_path, "UPDATE scans SET share_token = ? WHERE id = ?", (token, scan_id))
    return rowcount > 0


def get_scan_share_token(db_path, scan_id):
    row = _read_one(db_path, "SELECT share_token FROM scans WHERE id = ?", (scan_id,))
    return row[0] if row else None


def scan_exists(db_path, scan_id):
    return _read_one(db_path, "SELECT 1 FROM scans WHERE id = ?", (scan_id,)) is not None


def get_scan_by_share_token(db_path, token):
    row = _read_one(
        db_path,
        "SELECT id, created_at, label, summary, payload_json FROM scans WHERE share_token = ?",
        (token,),
    )
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "label": row["label"],
        "summary": row["summary"],
        "payload": json.loads(row["payload_json"]),
    }


# ---------------------------------------------------------------------------
# garage notes (car journal)
# ---------------------------------------------------------------------------
def save_garage_note(db_path, created_at, vin, plate, title, mileage, note, payload,
                     vehicle_id=None, attachment_name=None):
    _, note_id = _write(
        db_path,
        """
        INSERT INTO garage_notes
            (created_at, vin, plate, title, mileage, note, payload_json, vehicle_id, attachment_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, vin, plate, title, mileage, note, json.dumps(payload), vehicle_id, attachment_name),
    )
    return {
        "id": note_id,
        "created_at": created_at,
        "vin": vin,
        "plate": plate,
        "title": title,
        "mileage": mileage,
        "note": note,
        "vehicle_id": vehicle_id,
        "attachment_name": attachment_name,
    }


def update_garage_note(db_path, note_id, vin, plate, title, mileage, note):
    rowcount, _ = _write(
        db_path,
        """
        UPDATE garage_notes
        SET vin = ?, plate = ?, title = ?, mileage = ?, note = ?
        WHERE id = ?
        """,
        (vin, plate, title, mileage, note, note_id),
    )
    return rowcount > 0


def _note_row_to_dict(row, with_payload=True):
    note = {
        "id": row["id"],
        "created_at": row["created_at"],
        "vin": row["vin"],
        "plate": row["plate"],
        "title": row["title"],
        "mileage": row["mileage"],
        "note": row["note"],
        "vehicle_id": row["vehicle_id"],
        "attachment_name": row["attachment_name"],
    }
    if with_payload:
        note["payload"] = json.loads(row["payload_json"])
    return note


def get_recent_garage_notes(db_path, limit, vehicle_id=None, with_payload=True):
    """Newest-first garage notes.

    with_payload=False skips the stored scan snapshot that was captured with
    the note. Callers that only need the note text (maintenance estimates,
    exports, search) should pass False: the payload is by far the largest
    column in the table.
    """
    columns = "id, created_at, vin, plate, title, mileage, note, vehicle_id, attachment_name"
    if with_payload:
        columns += ", payload_json"

    if vehicle_id is not None:
        rows = _read(
            db_path,
            f"SELECT {columns} FROM garage_notes WHERE vehicle_id = ? ORDER BY id DESC LIMIT ?",
            (vehicle_id, limit),
        )
    else:
        rows = _read(
            db_path,
            f"SELECT {columns} FROM garage_notes ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return [_note_row_to_dict(row, with_payload) for row in rows]


def get_garage_note(db_path, note_id):
    row = _read_one(
        db_path,
        """
        SELECT id, created_at, vin, plate, title, mileage, note, vehicle_id,
               attachment_name, payload_json
        FROM garage_notes WHERE id = ?
        """,
        (note_id,),
    )
    return _note_row_to_dict(row) if row else None


def delete_garage_note(db_path, note_id):
    rowcount, _ = _write(db_path, "DELETE FROM garage_notes WHERE id = ?", (note_id,))
    return rowcount > 0


def get_all_attachment_names(db_path):
    rows = _read(
        db_path,
        "SELECT attachment_name FROM garage_notes WHERE attachment_name IS NOT NULL AND attachment_name != ''",
    )
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# fuel logs
# ---------------------------------------------------------------------------
def save_fuel_log(db_path, created_at, mileage_km, liters, cost, notes, vehicle_id=None):
    _, log_id = _write(
        db_path,
        """
        INSERT INTO fuel_logs (created_at, mileage_km, liters, cost, notes, vehicle_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (created_at, mileage_km, liters, cost, notes, vehicle_id),
    )
    return {
        "id": log_id,
        "created_at": created_at,
        "mileage_km": mileage_km,
        "liters": liters,
        "cost": cost,
        "notes": notes,
        "vehicle_id": vehicle_id,
    }


def get_recent_fuel_logs(db_path, limit, vehicle_id=None):
    if vehicle_id is not None:
        rows = _read(
            db_path,
            """
            SELECT id, created_at, mileage_km, liters, cost, notes, vehicle_id
            FROM fuel_logs WHERE vehicle_id = ? ORDER BY mileage_km DESC LIMIT ?
            """,
            (vehicle_id, limit),
        )
    else:
        rows = _read(
            db_path,
            """
            SELECT id, created_at, mileage_km, liters, cost, notes, vehicle_id
            FROM fuel_logs ORDER BY mileage_km DESC LIMIT ?
            """,
            (limit,),
        )
    return [dict(row) for row in rows]


def delete_fuel_log(db_path, log_id):
    rowcount, _ = _write(db_path, "DELETE FROM fuel_logs WHERE id = ?", (log_id,))
    return rowcount > 0


def get_highest_fuel_log_mileage_km(db_path):
    row = _read_one(db_path, "SELECT MAX(mileage_km) FROM fuel_logs")
    return row[0] if row and row[0] is not None else None


# ---------------------------------------------------------------------------
# trips
# ---------------------------------------------------------------------------
def save_trip(db_path, created_at, start_mileage_km, end_mileage_km, purpose, notes, vehicle_id=None):
    _, trip_id = _write(
        db_path,
        """
        INSERT INTO trips (created_at, start_mileage_km, end_mileage_km, purpose, notes, vehicle_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (created_at, start_mileage_km, end_mileage_km, purpose, notes, vehicle_id),
    )
    return {
        "id": trip_id,
        "created_at": created_at,
        "start_mileage_km": start_mileage_km,
        "end_mileage_km": end_mileage_km,
        "purpose": purpose,
        "notes": notes,
        "vehicle_id": vehicle_id,
    }


def get_recent_trips(db_path, limit, vehicle_id=None):
    if vehicle_id is not None:
        rows = _read(
            db_path,
            """
            SELECT id, created_at, start_mileage_km, end_mileage_km, purpose, notes, vehicle_id
            FROM trips WHERE vehicle_id = ? ORDER BY id DESC LIMIT ?
            """,
            (vehicle_id, limit),
        )
    else:
        rows = _read(
            db_path,
            """
            SELECT id, created_at, start_mileage_km, end_mileage_km, purpose, notes, vehicle_id
            FROM trips ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
    return [dict(row) for row in rows]


def delete_trip(db_path, trip_id):
    rowcount, _ = _write(db_path, "DELETE FROM trips WHERE id = ?", (trip_id,))
    return rowcount > 0


def get_highest_trip_mileage_km(db_path):
    row = _read_one(db_path, "SELECT MAX(end_mileage_km) FROM trips")
    return row[0] if row and row[0] is not None else None


# ---------------------------------------------------------------------------
# vehicles
# ---------------------------------------------------------------------------
def save_vehicle(db_path, created_at, nickname, vin, plate, brand="", model="", year="", tank_liters=None):
    _, vehicle_id = _write(
        db_path,
        """
        INSERT INTO vehicles (created_at, nickname, vin, plate, brand, model, year, tank_liters)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, nickname, vin, plate, brand, model, year, tank_liters),
    )
    return {
        "id": vehicle_id, "created_at": created_at, "nickname": nickname, "vin": vin,
        "plate": plate, "brand": brand, "model": model, "year": year, "tank_liters": tank_liters,
    }


def get_vehicles(db_path):
    rows = _read(
        db_path,
        """
        SELECT id, created_at, nickname, vin, plate, brand, model, year, tank_liters
        FROM vehicles ORDER BY id ASC
        """,
    )
    return [dict(row) for row in rows]


def delete_vehicle(db_path, vehicle_id):
    rowcount, _ = _write(db_path, "DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    return rowcount > 0
