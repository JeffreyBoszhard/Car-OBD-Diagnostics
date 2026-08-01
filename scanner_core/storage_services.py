# Made by Boszhard Development
import json
import sqlite3
from pathlib import Path


def db_path_from_file(app_file):
    return Path(app_file).with_name("scanner_config.db")


def init_storage(db_path):
    with sqlite3.connect(db_path) as db:
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
        existing_columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(garage_notes)").fetchall()
        }
        if "vin" not in existing_columns:
            db.execute("ALTER TABLE garage_notes ADD COLUMN vin TEXT NOT NULL DEFAULT ''")
        if "plate" not in existing_columns:
            db.execute("ALTER TABLE garage_notes ADD COLUMN plate TEXT NOT NULL DEFAULT ''")
        db.commit()


def get_setting(db_path, key, default=None):
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()

    return row[0] if row else default


def set_setting(db_path, key, value):
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        db.commit()

    return True


def save_scan_snapshot(db_path, created_at, label, summary, payload):
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO scans (created_at, label, summary, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (created_at, label, summary, json.dumps(payload)),
        )
        db.commit()

    return {
        "created_at": created_at,
        "label": label,
        "summary": summary,
    }


def get_recent_scans(db_path, limit):
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            """
            SELECT id, created_at, label, summary, payload_json
            FROM scans
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    scans = []
    for row in rows:
        scans.append(
            {
                "id": row[0],
                "created_at": row[1],
                "label": row[2],
                "summary": row[3],
                "payload": json.loads(row[4]),
            }
        )

    return scans


def save_garage_note(db_path, created_at, vin, plate, title, mileage, note, payload):
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO garage_notes (created_at, vin, plate, title, mileage, note, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at, vin, plate, title, mileage, note, json.dumps(payload)),
        )
        db.commit()

    return {
        "id": None,
        "created_at": created_at,
        "vin": vin,
        "plate": plate,
        "title": title,
        "mileage": mileage,
        "note": note,
    }


def update_garage_note(db_path, note_id, vin, plate, title, mileage, note):
    with sqlite3.connect(db_path) as db:
        cursor = db.execute(
            """
            UPDATE garage_notes
            SET vin = ?, plate = ?, title = ?, mileage = ?, note = ?
            WHERE id = ?
            """,
            (vin, plate, title, mileage, note, note_id),
        )
        db.commit()

    return cursor.rowcount > 0


def get_recent_garage_notes(db_path, limit):
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            """
            SELECT id, created_at, vin, plate, title, mileage, note, payload_json
            FROM garage_notes
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    notes = []
    for row in rows:
        notes.append(
            {
                "id": row[0],
                "created_at": row[1],
                "vin": row[2],
                "plate": row[3],
                "title": row[4],
                "mileage": row[5],
                "note": row[6],
                "payload": json.loads(row[7]),
            }
        )

    return notes


def delete_garage_note(db_path, note_id):
    with sqlite3.connect(db_path) as db:
        cursor = db.execute(
            "DELETE FROM garage_notes WHERE id = ?",
            (note_id,),
        )
        db.commit()

    return cursor.rowcount > 0


def delete_scan_snapshot(db_path, scan_id):
    with sqlite3.connect(db_path) as db:
        cursor = db.execute(
            "DELETE FROM scans WHERE id = ?",
            (scan_id,),
        )
        db.commit()

    return cursor.rowcount > 0


def load_mysql_driver():
    try:
        return "mysql.connector", importlib.import_module("mysql.connector")
    except Exception:
        try:
            return "pymysql", importlib.import_module("pymysql")
        except Exception as exc:
            raise RuntimeError("Install mysql-connector-python or pymysql to use MySQL storage.") from exc


def mysql_config_from_setup(setup):
    mysql = setup.get("mysql", {}) if isinstance(setup, dict) else {}
    return {
        "host": str(mysql.get("host") or "").strip(),
        "port": int(mysql.get("port") or 3306),
        "database": str(mysql.get("database") or "").strip(),
        "user": str(mysql.get("user") or "").strip(),
        "password": str(mysql.get("password") or ""),
    }


def mysql_connect(mysql_config):
    driver_name, driver = load_mysql_driver()
    config = dict(mysql_config)
    if not config.get("host") or not config.get("database") or not config.get("user"):
        raise RuntimeError("Enter MySQL host, database and user before saving MySQL setup.")
    if driver_name == "mysql.connector":
        return driver.connect(**config)
    return driver.connect(
        host=config["host"],
        port=config["port"],
        database=config["database"],
        user=config["user"],
        password=config.get("password", ""),
        charset="utf8mb4",
        autocommit=False,
    )


def init_mysql_storage(mysql_config):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                created_at VARCHAR(32) NOT NULL,
                label VARCHAR(255) NOT NULL,
                summary TEXT NOT NULL,
                payload_json LONGTEXT NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS garage_notes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                created_at VARCHAR(32) NOT NULL,
                vin VARCHAR(32) NOT NULL DEFAULT '',
                plate VARCHAR(32) NOT NULL DEFAULT '',
                title VARCHAR(255) NOT NULL,
                mileage VARCHAR(64) NOT NULL,
                note TEXT NOT NULL,
                payload_json LONGTEXT NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        db.commit()
    finally:
        db.close()

    return True


def save_scan_snapshot_mysql(mysql_config, created_at, label, summary, payload):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO scans (created_at, label, summary, payload_json) VALUES (%s, %s, %s, %s)",
            (created_at, label, summary, json.dumps(payload)),
        )
        db.commit()
        scan_id = getattr(cursor, "lastrowid", None)
    finally:
        db.close()

    return {"id": scan_id, "created_at": created_at, "label": label, "summary": summary}


def get_recent_scans_mysql(mysql_config, limit):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, created_at, label, summary, payload_json FROM scans ORDER BY id DESC LIMIT %s",
            (int(limit),),
        )
        rows = cursor.fetchall()
    finally:
        db.close()

    return [
        {"id": row[0], "created_at": row[1], "label": row[2], "summary": row[3], "payload": json.loads(row[4])}
        for row in rows
    ]


def delete_scan_snapshot_mysql(mysql_config, scan_id):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM scans WHERE id = %s", (int(scan_id),))
        db.commit()
        deleted = cursor.rowcount > 0
    finally:
        db.close()
    return deleted


def save_garage_note_mysql(mysql_config, created_at, vin, plate, title, mileage, note, payload):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO garage_notes (created_at, vin, plate, title, mileage, note, payload_json) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (created_at, vin, plate, title, mileage, note, json.dumps(payload)),
        )
        db.commit()
        note_id = getattr(cursor, "lastrowid", None)
    finally:
        db.close()

    return {"id": note_id, "created_at": created_at, "vin": vin, "plate": plate, "title": title, "mileage": mileage, "note": note}


def update_garage_note_mysql(mysql_config, note_id, vin, plate, title, mileage, note):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE garage_notes SET vin = %s, plate = %s, title = %s, mileage = %s, note = %s WHERE id = %s",
            (vin, plate, title, mileage, note, int(note_id)),
        )
        db.commit()
        updated = cursor.rowcount > 0
    finally:
        db.close()
    return updated


def get_recent_garage_notes_mysql(mysql_config, limit):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, created_at, vin, plate, title, mileage, note, payload_json FROM garage_notes ORDER BY id DESC LIMIT %s",
            (int(limit),),
        )
        rows = cursor.fetchall()
    finally:
        db.close()

    return [
        {"id": row[0], "created_at": row[1], "vin": row[2], "plate": row[3], "title": row[4], "mileage": row[5], "note": row[6], "payload": json.loads(row[7])}
        for row in rows
    ]


def delete_garage_note_mysql(mysql_config, note_id):
    db = mysql_connect(mysql_config)
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM garage_notes WHERE id = %s", (int(note_id),))
        db.commit()
        deleted = cursor.rowcount > 0
    finally:
        db.close()
    return deleted
