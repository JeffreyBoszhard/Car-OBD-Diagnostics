"""Storage and HTTP tests against a temporary database.

Every test points app_state.DB_PATH at a throwaway file, so running the suite
never touches the scanner_config.db the real dashboard uses.
"""
import json

import pytest

from scanner_core import app_state, storage_services


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "test_scanner.db"
    monkeypatch.setattr(app_state, "DB_PATH", path)
    storage_services.init_storage(path)
    return path


def test_init_storage_is_idempotent(db):
    # Running it twice is what happens on every app start.
    storage_services.init_storage(db)
    storage_services.init_storage(db)
    with storage_services.connect(db) as handle:
        tables = {row[0] for row in handle.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"settings", "scans", "garage_notes", "fuel_logs", "vehicles", "trips"} <= tables


def test_settings_round_trip(db):
    assert storage_services.get_setting(db, "missing", "fallback") == "fallback"
    storage_services.set_setting(db, "obd_port", "COM5")
    assert storage_services.get_setting(db, "obd_port") == "COM5"
    # An existing key is updated, not duplicated.
    storage_services.set_setting(db, "obd_port", "COM7")
    assert storage_services.get_setting(db, "obd_port") == "COM7"
    assert storage_services.get_all_settings(db)["obd_port"] == "COM7"


def test_scan_snapshot_save_list_delete(db):
    saved = storage_services.save_scan_snapshot(db, "2026-01-01 10:00:00", "Label", "Summary", {"health": {"score": 88}})
    scans = storage_services.get_recent_scans(db, 10)
    assert len(scans) == 1
    assert scans[0]["payload"]["health"]["score"] == 88
    assert storage_services.delete_scan_snapshot(db, saved["id"]) is True
    assert storage_services.get_recent_scans(db, 10) == []
    assert storage_services.delete_scan_snapshot(db, saved["id"]) is False


def test_share_token_lookup(db):
    saved = storage_services.save_scan_snapshot(db, "2026-01-01 10:00:00", "L", "S", {"ok": True})
    assert storage_services.set_scan_share_token(db, saved["id"], "tok123") is True
    found = storage_services.get_scan_by_share_token(db, "tok123")
    assert found["payload"] == {"ok": True}
    # Revoking makes the link stop working.
    storage_services.set_scan_share_token(db, saved["id"], None)
    assert storage_services.get_scan_by_share_token(db, "tok123") is None


def test_garage_note_payload_can_be_skipped(db):
    storage_services.save_garage_note(
        db, "2026-01-01 10:00:00", "", "12AB34", "Oil change", "150000", "Changed oil and filter",
        {"big": "payload"}, None, None,
    )
    light = storage_services.get_recent_garage_notes(db, 10, with_payload=False)
    assert "payload" not in light[0]
    assert light[0]["note"] == "Changed oil and filter"

    full = storage_services.get_recent_garage_notes(db, 10, with_payload=True)
    assert full[0]["payload"] == {"big": "payload"}


def test_highest_mileage_queries(db):
    storage_services.save_fuel_log(db, "2026-01-01 10:00:00", 120000.0, 45.0, 80.0, "")
    storage_services.save_fuel_log(db, "2026-02-01 10:00:00", 123500.0, 42.0, 78.0, "")
    storage_services.save_trip(db, "2026-02-02 10:00:00", 123500.0, 123900.0, "Trip", "")
    assert storage_services.get_highest_fuel_log_mileage_km(db) == 123500.0
    assert storage_services.get_highest_trip_mileage_km(db) == 123900.0


def test_vehicle_crud(db):
    vehicle = storage_services.save_vehicle(db, "2026-01-01 10:00:00", "Daily", "", "12AB34", "VW", "Golf", "2015", 50.0)
    assert storage_services.get_vehicles(db)[0]["nickname"] == "Daily"
    assert storage_services.delete_vehicle(db, vehicle["id"]) is True
    assert storage_services.get_vehicles(db) == []


# ---------------------------------------------------------------------------
# attachment validation
# ---------------------------------------------------------------------------
def test_attachment_name_validation():
    from scanner_core.attachment_services import is_valid_attachment_name

    assert is_valid_attachment_name("0123456789abcdef0123456789abcdef.jpg") is True
    assert is_valid_attachment_name("../../etc/passwd") is False
    assert is_valid_attachment_name("photo.jpg") is False
    assert is_valid_attachment_name("0123456789abcdef0123456789abcdef.exe") is False
    assert is_valid_attachment_name("") is False


def test_attachment_rejects_non_images(tmp_path, monkeypatch):
    from scanner_core import attachment_services

    monkeypatch.setattr(app_state, "UPLOAD_DIR", tmp_path / "uploads")

    name, error = attachment_services.save_attachment_bytes(b"not an image at all")
    assert name is None and "JPEG" in error

    # A real (tiny) PNG is accepted and stored under a generated name.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    name, error = attachment_services.save_attachment_bytes(png)
    assert error == ""
    assert name.endswith(".png")
    assert (tmp_path / "uploads" / name).exists()


def test_attachment_size_limit(tmp_path, monkeypatch):
    from scanner_core import attachment_services

    monkeypatch.setattr(app_state, "UPLOAD_DIR", tmp_path / "uploads")
    oversized = b"\xff\xd8\xff" + (b"x" * (7 * 1024 * 1024))
    name, error = attachment_services.save_attachment_bytes(oversized)
    assert name is None and "MB" in error


# ---------------------------------------------------------------------------
# PIN lock
# ---------------------------------------------------------------------------
def test_pin_token_validation(monkeypatch):
    from scanner_core import auth_services

    monkeypatch.setattr(auth_services, "ACCESS_PIN", "4321")
    token = auth_services.issue_token()
    assert auth_services.token_is_valid(token) is True
    assert auth_services.token_is_valid("garbage") is False
    assert auth_services.token_is_valid(token + "x") is False
    assert auth_services.pin_is_correct("4321") is True
    assert auth_services.pin_is_correct("0000") is False


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_state, "DB_PATH", tmp_path / "api_scanner.db")
    monkeypatch.setattr(app_state, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_state, "DASHBOARD_SETUP_PATH", tmp_path / "dashboard_setup.json")
    monkeypatch.setattr(app_state, "DASHBOARD_RUNTIME_PATH", tmp_path / "dashboard_runtime.json")
    storage_services.init_storage(app_state.DB_PATH)

    import app as app_module

    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "app.js" in body
    # The removed branding and the removed mode must not come back.
    assert "Boszhard" not in body
    assert "SAFE Mode" not in body


def test_status_and_data_endpoints(client):
    for path in ("/api/status", "/api/data", "/api/config", "/api/extended", "/api/stream/info"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.is_json, path


def test_clearing_codes_requires_confirmation(client):
    response = client.post("/api/clear", json={})
    assert response.status_code == 400
    assert "confirmation" in response.get_json()["message"].lower()


def test_clearing_codes_without_a_connection_is_reported(client):
    response = client.post("/api/clear", json={"confirm": "YES"})
    # No adapter in CI: the attempt is refused, but for the right reason.
    assert response.status_code == 400
    assert "connection" in response.get_json()["message"].lower()


def test_uploads_route_rejects_path_traversal(client):
    assert client.get("/uploads/..%2F..%2Fconfig.py").status_code == 404
    assert client.get("/uploads/notarealname.jpg").status_code == 404


def test_garage_note_requires_note_text(client):
    response = client.post("/api/garage-notes", json={"plate": "12AB34", "note": ""})
    assert response.status_code == 400


def test_garage_note_round_trip(client):
    response = client.post("/api/garage-notes", json={
        "plate": "12AB34",
        "title": "Brake service",
        "mileage": "150000",
        "note": "Replaced front pads and discs.",
    })
    assert response.status_code == 200
    assert response.get_json()["success"] is True

    listed = client.get("/api/garage-notes").get_json()
    assert listed[0]["title"] == "Brake service"
    # The list must not carry the heavy scan snapshot per note.
    assert "payload" not in listed[0]


def test_runtime_thresholds_are_saved_as_numbers(client):
    response = client.post("/api/runtime", json={"warning_thresholds": {"coolant_temp_c": "105"}})
    assert response.status_code == 200
    saved = response.get_json()["runtime_state"]["warning_thresholds"]
    assert saved["coolant_temp_c"] == 105.0


def test_unexpected_errors_do_not_leak_internals():
    """The catch-all handler must not hand internals to the browser.

    The handler is called directly rather than through a test route: adding a
    route after the app has served a request is not allowed in Flask 3, and
    what matters here is the handler's own output.
    """
    import app as app_module

    with app_module.app.test_request_context("/api/anything"):
        response, status = app_module.handle_unexpected_error(
            RuntimeError("secret path C:/private/thing.py")
        )

    assert status == 500
    body = json.dumps(response.get_json())
    assert "secret path" not in body
    assert "System page" in body


def test_http_errors_keep_their_own_message():
    import app as app_module
    from werkzeug.exceptions import NotFound

    with app_module.app.test_request_context("/api/anything"):
        response, status = app_module.handle_unexpected_error(NotFound())

    assert status == 404
    assert response.get_json()["success"] is False
