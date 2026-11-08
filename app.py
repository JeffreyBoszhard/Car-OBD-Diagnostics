"""Flask application entry point.

This file wires up the app: Flask routes, request-scoped helpers (language
and units cookies, i18n/JSON localisation), the optional PIN lock and the
startup sequence for the background threads. The actual scanner logic (OBD
connection, live data, DTC scanning, VIN/RDW lookups, reports, settings,
unit conversion...) lives in scanner_core/ so this file stays a thin routing
layer over that logic.

Live data reaches the dashboard over /api/stream (server-sent events) rather
than by polling: one background publisher builds each frame and every open
tab is handed the same one. The REST endpoints are all still there for
scripts, for the first paint and for a browser whose unit selection differs
from the dashboard default.
"""
import json
import os
import threading
import time

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, url_for
from werkzeug.exceptions import HTTPException

from changelog import get_changelog_since
from config import (
    ACCESS_SESSION_MAX_AGE,
    APP_VERSION,
    DEBUG,
    OBD_CONNECT_ATTEMPTS,
    OBD_CONNECT_RETRY_DELAY,
    OBD_CONNECT_TIMEOUT,
    POLL_INTERVAL,
    SERVER_HOST,
    SERVER_PORT,
    STREAM_INTERVAL,
    UPDATE_DOWNLOAD_URL,
    UPLOAD_MAX_BYTES,
)
from scanner_core import app_state, event_services
from scanner_core.app_state import now_time
from scanner_core.attachment_services import (
    ensure_upload_dir,
    is_valid_attachment_name,
    save_attachment_from_data_uri,
    save_attachment_from_upload,
)
from scanner_core.auth_services import (
    COOKIE_NAME as UNLOCK_COOKIE,
    PUBLIC_ENDPOINTS,
    issue_token,
    pin_is_correct,
    pin_required,
    token_is_valid,
)
from scanner_core.background_loops import start_background_threads
from scanner_core.bluetooth_services import (
    bind_rfcomm_device,
    bluetooth_bind_supported,
    list_paired_bluetooth_devices,
    release_rfcomm_device,
)
from scanner_core.connection_services import connect_obd
from scanner_core.demo_services import (
    build_demo_vehicle_snapshot,
    get_demo_preset,
    get_demo_presets,
    normalize_demo_preset,
)
from scanner_core.dtc_services import (
    build_demo_readiness_for_current_clear_state,
    reset_dtc_state,
    reset_readiness_state,
    run_full_vehicle_scan,
    scan_dtc_codes,
)
from scanner_core.error_services import friendly_message, log_error, read_error_log_tail
from scanner_core.extended_pid_services import get_extended_data, refresh_extended_data
from scanner_core.fuel_log_services import delete_fuel_log_entry, get_fuel_log_history, save_fuel_log_entry
from scanner_core.garage_services import (
    filter_garage_notes,
    normalize_garage_plate,
    render_garage_notes_export_html,
    validate_garage_note_identity,
)
from scanner_core.health_services import build_pid_support_summary, enrich_connection_quality
from scanner_core.maintenance_services import get_maintenance_reminder
from scanner_core.obd_bridge import get_command, obd
from scanner_core.obd_services import (
    connection_quality_snapshot as build_connection_quality_snapshot,
    invalidate_port_cache,
    list_serial_ports,
    normalize_port_name,
    run_connection_test,
)
from scanner_core.report_share_services import (
    create_or_get_share_link,
    get_shared_scan,
    revoke_share_link,
    scan_exists,
)
from scanner_core.scan_payload_services import (
    apply_export_preset,
    current_scan_payload,
    delete_garage_note,
    delete_scan_snapshot,
    get_garage_note,
    get_recent_garage_notes,
    get_recent_scans,
    render_export_html,
    render_scan_csv,
    save_garage_note_snapshot,
    save_scan_snapshot,
    update_garage_note,
)
from scanner_core.session_services import build_scanner_session_state
from scanner_core.settings_services import (
    POLL_PROFILES,
    apply_demo_preset_state,
    get_configured_port,
    get_demo_mode_enabled,
    get_demo_preset_name,
    get_poll_profile,
    get_setting,
    init_config_db,
    normalize_poll_profile,
    set_demo_mode_enabled,
    set_demo_preset_name,
    set_poll_profile_name,
    set_setting,
)
from scanner_core.setup_services import (
    fetch_latest_github_version,
    get_dashboard_setup,
    get_runtime_state,
    infer_obd_bus_mode,
    is_newer_version,
    save_json_file,
)
from scanner_core.translation import (
    DEFAULT_THEME,
    DEFAULT_UNITS,
    LANGUAGE_OPTIONS,
    THEME_OPTIONS,
    UNIT_OPTIONS,
    get_language,
    get_theme,
    get_translations,
    get_units,
    localize_payload,
)
from scanner_core.trip_log_services import delete_trip_log_entry, get_trip_log_history, save_trip_log_entry
from scanner_core.trip_services import reset_trip_state
from scanner_core.units_services import apply_unit_conversions
from scanner_core.vehicle_services import (
    delete_vehicle,
    get_active_vehicle,
    get_active_vehicle_id,
    get_vehicles,
    save_vehicle,
    set_active_vehicle_id,
)
from scanner_core.vin_services import (
    normalize_vin,
    refresh_plate_profile,
    refresh_vin_profile,
    reset_vehicle_profile,
    set_manual_vin,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_BYTES + (256 * 1024)


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------
def current_language():
    return get_language(request.cookies.get("obd_lang", "en"))


def current_units():
    return get_units(request.cookies.get("obd_units", DEFAULT_UNITS))


def current_theme():
    return get_theme(request.cookies.get("obd_theme", DEFAULT_THEME))


def localized_jsonify(payload, status_code=200):
    return jsonify(localize_payload(payload, current_language())), status_code


@app.context_processor
def inject_translations():
    lang = current_language()
    units = current_units()
    translations = get_translations(lang)
    translations["meta"]["title"] = f"OBD Scanner {APP_VERSION}"
    translations["partials"]["startup"]["title"] = f"OBD Scanner {APP_VERSION}"
    translations["partials"]["sidebar"]["brand_name"] = f"OBD Scanner {APP_VERSION}"
    if units == "imperial":
        translations["live"]["speed_unit"] = "mph"
        translations["garage"]["mileage_placeholder"] = translations["garage"]["mileage_placeholder"].replace("km", "mi")
        translations["garage"]["fuel_log_mileage_label"] = translations["garage"]["fuel_log_mileage_label"].replace("km", "mi")
        translations["garage"]["fuel_log_liters_label"] = translations["garage"]["fuel_log_liters_label"].replace("L", "gal")

    return {
        "lang": lang,
        "language_options": LANGUAGE_OPTIONS,
        "units": units,
        "unit_options": UNIT_OPTIONS,
        "theme": current_theme(),
        "theme_options": THEME_OPTIONS,
        "i18n": translations,
        "js_translations": translations.get("js", {}),
        "app_version": APP_VERSION,
        "stream_interval_ms": int(STREAM_INTERVAL * 1000),
    }


@app.before_request
def enforce_pin_lock():
    """Hold everything behind the PIN screen when a PIN is configured."""
    if not pin_required():
        return None
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if token_is_valid(request.cookies.get(UNLOCK_COOKIE)):
        return None

    if request.path.startswith("/api/"):
        return jsonify({"success": False, "locked": True, "message": "Enter the dashboard PIN."}), 401
    return render_template("lock.html"), 401


@app.after_request
def localize_json_response(response):
    # Streaming responses must not be buffered to localise them, and they
    # carry no translatable server text anyway.
    if response.direct_passthrough:
        return response
    if response.is_json:
        try:
            payload = response.get_json(silent=True)
            localized = localize_payload(payload, current_language())
            response.set_data(json.dumps(localized))
        except Exception:
            pass
    return response


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    try:
        return render_template("dashboard.html")
    except Exception as e:
        log_error("Render dashboard", e, affects_status=False)
        return "Dashboard could not load. Check the console.", 500


@app.route("/lock")
def lock_screen():
    return render_template("lock.html")


@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    payload = request.get_json(silent=True) or request.form or {}
    if not pin_is_correct(payload.get("pin")):
        # A deliberate small delay: this endpoint is the only thing standing
        # between a garage network and a scanner that can clear fault codes.
        time.sleep(0.6)
        return jsonify({"success": False, "message": "That PIN is not correct."}), 401

    response = jsonify({"success": True})
    response.set_cookie(
        UNLOCK_COOKIE,
        issue_token(),
        max_age=ACCESS_SESSION_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )
    return response


# ---------------------------------------------------------------------------
# live data stream
# ---------------------------------------------------------------------------
@app.route("/api/stream")
def api_stream():
    """Server-sent events carrying gauge, live-data and status frames."""
    subscriber = event_services.subscribe()
    response = Response(
        event_services.stream(subscriber),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["Connection"] = "keep-alive"
    # Stops nginx and friends from buffering the stream into uselessness.
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/api/stream/info")
def api_stream_info():
    return jsonify({
        "units": str(get_dashboard_setup().get("units") or "metric"),
        "interval_ms": int(STREAM_INTERVAL * 1000),
        "subscribers": event_services.subscriber_count(),
    })


# ---------------------------------------------------------------------------
# changelog / updates
# ---------------------------------------------------------------------------
@app.route("/api/changelog")
def api_changelog():
    seen_version = get_setting("last_seen_changelog_version", "")
    entries = get_changelog_since(seen_version)
    return jsonify({
        "success": True,
        "current_version": APP_VERSION,
        "seen_version": seen_version,
        "should_show": seen_version != APP_VERSION,
        "changelog": entries,
    })


@app.route("/api/changelog/ack", methods=["POST"])
def api_changelog_ack():
    set_setting("last_seen_changelog_version", APP_VERSION)
    return jsonify({"success": True})


@app.route("/api/update-check")
def api_update_check():
    try:
        latest_version = fetch_latest_github_version()
        update_available = bool(latest_version and is_newer_version(latest_version, APP_VERSION))
        return jsonify({
            "success": True,
            "current_version": APP_VERSION,
            "latest_version": latest_version or APP_VERSION,
            "update_available": update_available,
            "download_url": UPDATE_DOWNLOAD_URL,
        })
    except Exception as e:
        log_error("Update check", e, affects_status=False)
        return jsonify({
            "success": False,
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "update_available": False,
            "download_url": UPDATE_DOWNLOAD_URL,
            "message": "Update check could not be completed.",
        })


# ---------------------------------------------------------------------------
# setup / runtime state
# ---------------------------------------------------------------------------
@app.route("/api/setup", methods=["GET", "POST"])
def api_setup():
    if request.method == "GET":
        return jsonify(get_dashboard_setup())

    data = request.get_json(silent=True) or {}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    current = get_dashboard_setup()
    language = get_language(data.get("language") or current.get("language") or current_language())
    units = get_units(data.get("units") or current.get("units") or current_units())
    setup = {
        **current,
        "completed": True,
        "language": language,
        "units": units,
        "created_at": current.get("created_at") or now,
        "updated_at": now,
    }
    save_json_file(app_state.DASHBOARD_SETUP_PATH, setup)
    response = jsonify({"success": True, "setup": setup})
    response.set_cookie("obd_lang", language, max_age=31536000, samesite="Lax")
    response.set_cookie("obd_units", units, max_age=31536000, samesite="Lax")
    return response


@app.route("/api/runtime", methods=["GET", "POST"])
def api_runtime():
    if request.method == "GET":
        return jsonify(get_runtime_state())
    data = request.get_json(silent=True) or {}
    state = get_runtime_state()
    if isinstance(data.get("warning_thresholds"), dict):
        merged = {**state.get("warning_thresholds", {})}
        for key, value in data["warning_thresholds"].items():
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                continue
        state["warning_thresholds"] = merged
    save_json_file(app_state.DASHBOARD_RUNTIME_PATH, state)
    return jsonify({"success": True, "runtime_state": state})


# ---------------------------------------------------------------------------
# status / live data
# ---------------------------------------------------------------------------
@app.route("/api/status")
def api_status():
    with app_state.state_lock:
        status = dict(app_state.obd_status)
    status["limited_mode"] = bool(get_poll_profile()["limited"])
    connection_quality = (
        {
            "phase": "Demo mode",
            "adapter_connected": True,
            "port_powered": True,
            "car_connected": True,
            "live_data_active": True,
        }
        if status.get("demo_mode")
        else build_connection_quality_snapshot(app_state.connection, status.get("connecting"), status.get("error"))
    )
    connection_quality = enrich_connection_quality(status, connection_quality)
    return jsonify({
        **status,
        "poll_profile": get_poll_profile(),
        "connection_quality": connection_quality,
        "obd_bus_mode": infer_obd_bus_mode(status.get("protocol")),
        "dashboard_setup": get_dashboard_setup(),
        "runtime_state": get_runtime_state(),
        "session_state": build_scanner_session_state(
            status,
            connection_quality,
            status.get("connection_hint"),
        ),
    })


@app.route("/api/gauges")
def api_gauges():
    with app_state.state_lock:
        status = dict(app_state.obd_status)
        demo_enabled = bool(status.get("demo_mode")) or get_demo_mode_enabled()

        if demo_enabled:
            demo_speed = float(app_state.demo_drive_state.get("speed_kmh", 0.0))
            demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
            demo_snapshot = build_demo_vehicle_snapshot(demo_speed, demo_preset)
            vehicle = {
                "rpm": dict(demo_snapshot.get("rpm", {"label": "RPM", "value": "N/A"})),
                "speed": dict(demo_snapshot.get("speed", {"label": "Speed", "value": "N/A"})),
            }
        else:
            vehicle = {
                "rpm": dict(app_state.vehicle_data.get("rpm", {"label": "RPM", "value": "N/A"})),
                "speed": dict(app_state.vehicle_data.get("speed", {"label": "Speed", "value": "N/A"})),
            }

    return jsonify({
        "connected": bool(status.get("connected")),
        "demo_mode": bool(demo_enabled),
        "vehicle": apply_unit_conversions(vehicle, current_units()),
    })


@app.route("/api/data")
def api_data():
    payload = current_scan_payload(current_units())
    payload["units"] = current_units()
    with app_state.state_lock:
        payload["dtc_status"] = dict(app_state.dtc_status)
    return jsonify(payload)


@app.route("/api/extended")
def api_extended():
    return jsonify(get_extended_data())


@app.route("/api/extended/refresh", methods=["POST"])
def api_extended_refresh():
    try:
        return jsonify({"success": True, "extended": refresh_extended_data(get_demo_mode_enabled())})
    except Exception as e:
        log_error("Extended PIDs", e, affects_status=False)
        return jsonify({"success": False, "message": "Odometer and DPF values could not be read."}), 500


@app.route("/api/connection/test", methods=["POST"])
def api_connection_test():
    if get_demo_mode_enabled():
        return jsonify({
            "success": True,
            "phase": "Demo mode",
            "protocol": "Simulator",
            "steps": [
                {"name": "Adapter port detected", "ok": True, "detail": "Simulated adapter"},
                {"name": "Adapter responding", "ok": True, "detail": "Simulated ELM327 v1.5"},
                {"name": "Battery voltage at the port", "ok": True, "detail": "14.1 V simulated"},
                {"name": "OBD protocol detected", "ok": True, "detail": "Simulator"},
                {"name": "ECU responding", "ok": True, "detail": "Demo ECU"},
            ]
        })

    with app_state.state_lock:
        current_status = dict(app_state.obd_status)

    if app_state.connection and current_status.get("connected"):
        protocol = current_status.get("protocol") or "Unknown"
        port = current_status.get("current_port") or "auto-detect"
        return jsonify({
            "success": True,
            "phase": "Using current live connection",
            "protocol": protocol,
            "steps": [
                {"name": "Adapter port detected", "ok": True, "detail": port},
                {"name": "Adapter responding", "ok": True, "detail": current_status.get("adapter_name") or "Adapter online"},
                {"name": "Battery voltage at the port", "ok": current_status.get("adapter_voltage") is not None,
                 "detail": f"{current_status.get('adapter_voltage')} V" if current_status.get("adapter_voltage") is not None else "Not reported"},
                {"name": "OBD protocol detected", "ok": True, "detail": protocol},
                {"name": "ECU responding", "ok": True, "detail": "Live session already active"},
            ]
        })

    result = run_connection_test(
        obd,
        get_configured_port(),
        timeout=OBD_CONNECT_TIMEOUT,
        attempts=OBD_CONNECT_ATTEMPTS,
        retry_delay=OBD_CONNECT_RETRY_DELAY,
        get_command=get_command,
    )
    return jsonify(result), (200 if result.get("success") else 400)


@app.route("/api/trip/reset", methods=["POST"])
def api_trip_reset():
    reset_trip_state()
    payload = current_scan_payload(current_units())
    return jsonify({"success": True, "trip": payload.get("trip", {})})


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------
@app.route("/api/report")
def api_report():
    payload = current_scan_payload(current_units())
    return jsonify(payload.get("report", {}))


@app.route("/api/report/export.csv")
def api_report_export_csv():
    payload = apply_export_preset(current_scan_payload(current_units()), request.args.get("preset"))
    filename = f"obd-scan-report-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    return Response(
        render_scan_csv(payload),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/report/export")
def api_report_export():
    payload = apply_export_preset(current_scan_payload(current_units()), request.args.get("preset"))
    export_format = str(request.args.get("format") or "html").lower()
    if export_format == "csv":
        filename = f"obd-scan-report-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        return Response(render_scan_csv(payload), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})
    filename = f"obd-scan-report-{time.strftime('%Y%m%d-%H%M%S')}.html"
    return Response(
        render_export_html(payload),
        mimetype="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# fault codes
# ---------------------------------------------------------------------------
@app.route("/api/codes/scan", methods=["POST"])
def api_codes_scan():
    try:
        scan_dtc_codes()
        payload = current_scan_payload(current_units())
        with app_state.state_lock:
            payload["success"] = True
            payload["message"] = app_state.dtc_status["message"]
            payload["dtc_status"] = dict(app_state.dtc_status)
        return jsonify(payload)
    except Exception as e:
        log_error("Read DTC", e, affects_status=False)
        payload = current_scan_payload(current_units())
        with app_state.state_lock:
            payload["success"] = False
            payload["message"] = friendly_message(e, source="Read DTC")
            payload["dtc_status"] = dict(app_state.dtc_status)
        return jsonify(payload), 400


@app.route("/api/scan/vehicle/start", methods=["POST"])
def api_vehicle_scan_start():
    # Claim the scan and start the thread inside one lock hold: checking the
    # flag and then starting the thread separately let two quick requests
    # both pass the check and run two scans over the same serial link.
    with app_state.vehicle_scan_lock:
        if app_state.vehicle_scan_state["active"]:
            return jsonify({
                "success": False,
                "message": "A vehicle scan is already running.",
                "scan": dict(app_state.vehicle_scan_state),
            }), 409
        app_state.vehicle_scan_state["active"] = True
        app_state.vehicle_scan_state["cancelled"] = False
        threading.Thread(target=run_full_vehicle_scan, name="vehicle_scan", daemon=True).start()
        scan = dict(app_state.vehicle_scan_state)
        scan["parts"] = [dict(part) for part in app_state.vehicle_scan_state["parts"]]

    return jsonify({"success": True, "scan": scan})


@app.route("/api/scan/vehicle/status", methods=["GET"])
def api_vehicle_scan_status():
    with app_state.vehicle_scan_lock:
        scan = dict(app_state.vehicle_scan_state)
        scan["parts"] = [dict(part) for part in app_state.vehicle_scan_state["parts"]]
    payload = current_scan_payload(current_units())
    payload["success"] = True
    payload["scan"] = scan
    return jsonify(payload)


@app.route("/api/scan/vehicle/cancel", methods=["POST"])
def api_vehicle_scan_cancel():
    with app_state.vehicle_scan_lock:
        was_active = bool(app_state.vehicle_scan_state["active"])
        app_state.vehicle_scan_state["cancelled"] = True

        if was_active:
            for part in app_state.vehicle_scan_state["parts"]:
                if part.get("status") in {"waiting", "scanning"}:
                    part["status"] = "cancelled"
                    part["detail"] = ""
                    part["result"] = "Scan cancelled."
            message = "Cancelling scan..."
        else:
            message = "No active scan to cancel."

        scan = dict(app_state.vehicle_scan_state)
        scan["parts"] = [dict(part) for part in app_state.vehicle_scan_state["parts"]]

    with app_state.state_lock:
        if was_active:
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["message"] = "Vehicle diagnostic scan cancelled."

    return jsonify({"success": True, "message": message, "scan": scan})


@app.route("/api/clear", methods=["POST"])
def clear_codes():
    """Clear stored fault codes.

    Clearing is always available - there is no mode to switch on first - but
    it is never implicit: the request must carry an explicit confirmation,
    which the dashboard only sends after the user confirms the dialog that
    spells out what clearing does.
    """
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "YES":
        return jsonify({
            "success": False,
            "message": "Clearing fault codes needs an explicit confirmation."
        }), 400

    if get_demo_mode_enabled():
        with app_state.state_lock:
            demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
            app_state.demo_codes_cleared = True
            app_state.dtc_data = {
                "stored": [],
                "pending": [],
                "permanent": [],
            }
            app_state.freeze_frame_data = {
                "available": False,
                "values": {},
            }
            app_state.readiness_data = build_demo_readiness_for_current_clear_state(demo_preset, True)
            app_state.dtc_status["has_scan"] = True
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["last_scan"] = now_time()
            app_state.dtc_status["message"] = "Demo fault codes cleared."
        return jsonify({
            "success": True,
            "message": "Demo fault codes cleared. Change demo preset to load demo faults again."
        })

    if not app_state.connection or not app_state.connection.is_connected():
        return jsonify({
            "success": False,
            "message": "No OBD connection."
        }), 400

    try:
        with app_state.obd_lock:
            clear_command = get_command("CLEAR_DTC")
            if clear_command is None:
                raise RuntimeError("CLEAR_DTC command is not available.")

            app_state.connection.query(clear_command)
        with app_state.state_lock:
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["message"] = "Clear command sent. Run a new scan to verify the ECU is clean."
        return jsonify({
            "success": True,
            "message": "Clear command sent. Turn ignition off/on if needed and scan again."
        })

    except Exception as e:
        log_error("Clear fault codes", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": friendly_message(e, source="Clear fault codes")
        }), 500


# ---------------------------------------------------------------------------
# configuration / ports
# ---------------------------------------------------------------------------
@app.route("/api/config")
def api_config():
    return jsonify({
        "app_version": APP_VERSION,
        "obd_port": get_configured_port() or "",
        "detected_ports": list_serial_ports(),
        "demo_mode": get_demo_mode_enabled(),
        "limited_mode": bool(get_poll_profile()["limited"]),
        "poll_profile": get_poll_profile(),
        "poll_profiles": [{"id": key, **value} for key, value in POLL_PROFILES.items()],
        "demo_preset": get_demo_preset_name(),
        "demo_presets": get_demo_presets(),
        "poll_interval": POLL_INTERVAL,
        "pin_lock_enabled": pin_required(),
    })


@app.route("/api/poll-profile", methods=["POST"])
def api_poll_profile():
    try:
        payload = request.get_json(silent=True) or {}
        profile = normalize_poll_profile(payload.get("profile", "balanced"))

        if not set_poll_profile_name(profile):
            return jsonify({
                "success": False,
                "message": "Polling profile could not be saved."
            }), 500

        profile_meta = get_poll_profile()
        with app_state.state_lock:
            app_state.obd_status["limited_mode"] = bool(profile_meta["limited"])
            app_state.obd_status["last_update"] = time.strftime("%H:%M:%S")
            app_state.obd_status["user_message"] = f"{profile_meta['label']} polling profile active."
            status = dict(app_state.obd_status)

        return jsonify({
            "success": True,
            "poll_profile": profile_meta,
            "limited_mode": bool(profile_meta["limited"]),
            "status": status,
        })
    except Exception as e:
        log_error("Change polling profile", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Polling profile could not be changed."
        }), 500


@app.route("/api/config/ports")
def api_ports():
    return jsonify({
        "ports": list_serial_ports(force=request.args.get("refresh") == "1"),
        "selected": get_configured_port() or "",
    })


@app.route("/api/bluetooth/devices")
def api_bluetooth_devices():
    return jsonify({
        "supported": bluetooth_bind_supported(),
        "devices": list_paired_bluetooth_devices(),
    })


@app.route("/api/bluetooth/bind", methods=["POST"])
def api_bluetooth_bind():
    payload = request.get_json(silent=True) or {}
    device, error = bind_rfcomm_device(payload.get("mac", ""), payload.get("channel", 1))
    if not device:
        return jsonify({
            "success": False,
            "message": error or "Could not bind the Bluetooth adapter."
        }), 400

    invalidate_port_cache()
    return jsonify({
        "success": True,
        "device": device,
        "ports": list_serial_ports(force=True),
    })


@app.route("/api/bluetooth/release", methods=["POST"])
def api_bluetooth_release():
    payload = request.get_json(silent=True) or {}
    ok, error = release_rfcomm_device(payload.get("device", ""))
    if not ok:
        return jsonify({
            "success": False,
            "message": error or "Could not release the Bluetooth adapter."
        }), 400

    invalidate_port_cache()
    return jsonify({
        "success": True,
        "ports": list_serial_ports(force=True),
    })


@app.route("/api/config/port", methods=["POST"])
def set_obd_port():
    port = None
    try:
        payload = request.get_json(silent=True) or {}
        # Windows COM ports are conventionally uppercase; Linux/macOS device
        # paths (/dev/ttyUSB0, /dev/rfcomm0) are case-sensitive and must be
        # left exactly as detected.
        port = normalize_port_name(payload.get("port", ""))

        if not set_setting("obd_port", port):
            return jsonify({
                "success": False,
                "message": "Adapter port could not be saved."
            }), 500

        with app_state.obd_lock:
            try:
                if app_state.connection:
                    app_state.connection.close()
            except Exception as e:
                log_error("Close OBD connection", e)
            finally:
                app_state.connection = None

        with app_state.state_lock:
            app_state.vehicle_data = {}
            app_state.obd_status["connected"] = False
            app_state.obd_status["protocol"] = "Unknown"
            app_state.obd_status["current_port"] = port or None
            app_state.obd_status["error"] = "Adapter port changed. Reconnecting..."
            app_state.obd_status["user_message"] = (
                f"Adapter port saved to {port}. Reconnecting..." if port else "No adapter port selected. Reconnecting..."
            )
            app_state.obd_status["connecting"] = True
            app_state.obd_status["last_update"] = time.strftime("%H:%M:%S")

        app_state.connect_failure_streak = 0
        reset_vehicle_profile()
        reset_readiness_state()
        reset_dtc_state("Adapter port changed. Run a new fault code scan after reconnecting.")
        connect_obd()

        with app_state.state_lock:
            status = dict(app_state.obd_status)

        return jsonify({
            "success": True,
            "message": f"Adapter port saved: {port}" if port else "Port selection cleared. No adapter port selected.",
            "obd_port": port,
            "detected_ports": list_serial_ports(force=True),
            "status": status,
        })
    except Exception as e:
        log_error("Change COM port", e)
        return jsonify({
            "success": False,
            "message": friendly_message(e, source="Change COM port", port=port)
        }), 500


@app.route("/api/supported")
def supported_commands():
    try:
        support = build_pid_support_summary()
        return jsonify({
            **support,
            "standard_obd_only": True,
        })
    except Exception as e:
        log_error("Read supported commands", e, affects_status=False)
        return jsonify({
            "supported": [],
            "unsupported": [],
            "standard_obd_only": True,
        })


# ---------------------------------------------------------------------------
# demo mode
# ---------------------------------------------------------------------------
@app.route("/api/demo-mode", methods=["POST"])
def api_demo_mode():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    demo_preset = get_demo_preset_name()

    if not set_demo_mode_enabled(enabled):
        return jsonify({
            "success": False,
            "message": "Demo mode could not be saved."
        }), 500

    with app_state.obd_lock:
        try:
            if app_state.connection:
                app_state.connection.close()
        except Exception as e:
            log_error("Close OBD connection", e)
        finally:
            app_state.connection = None

    with app_state.state_lock:
        app_state.vehicle_data = {}
        app_state.demo_codes_cleared = False
        app_state.obd_status["demo_mode"] = enabled
        app_state.obd_status["current_port"] = "Demo mode" if enabled else get_configured_port()

    app_state.query_error_streak = 0
    app_state.connect_failure_streak = 0
    app_state.current_live_poll_interval = POLL_INTERVAL
    apply_demo_preset_state(demo_preset, reset_speed=True)
    reset_readiness_state()
    reset_dtc_state("Demo mode changed. Run a manual fault code scan again if needed.")
    reset_vehicle_profile()
    reset_trip_state()
    connect_obd()

    with app_state.state_lock:
        status = dict(app_state.obd_status)

    return jsonify({
        "success": True,
        "demo_mode": enabled,
        "demo_preset": demo_preset,
        "status": status,
    })


@app.route("/api/demo-mode/preset", methods=["POST"])
def api_demo_preset():
    payload = request.get_json(silent=True) or {}
    preset_name = normalize_demo_preset(payload.get("preset", "idle"))

    if not set_demo_preset_name(preset_name):
        return jsonify({
            "success": False,
            "message": "Demo preset could not be saved."
        }), 500

    preset_name, speed = apply_demo_preset_state(preset_name, reset_speed=True)

    with app_state.state_lock:
        app_state.demo_codes_cleared = False
        if app_state.obd_status.get("demo_mode"):
            app_state.obd_status["last_update"] = now_time()
            app_state.obd_status["user_message"] = f"Demo preset switched to {get_demo_preset(preset_name)[1]['label']}."

    reset_readiness_state()
    reset_dtc_state("Demo preset changed. Run a manual fault code scan again if needed.")
    with app_state.state_lock:
        app_state.freeze_frame_data["available"] = False
        app_state.freeze_frame_data["values"] = {}

    return jsonify({
        "success": True,
        "demo_preset": preset_name,
        "demo_presets": get_demo_presets(),
        "speed_kmh": speed,
    })


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
@app.route("/api/errors")
def api_errors():
    with app_state.state_lock:
        return jsonify(list(app_state.obd_status["recent_errors"]))


@app.route("/api/errors/log")
def api_error_log():
    """The tail of the rotating error log file."""
    try:
        limit = max(1, min(1000, request.args.get("lines", default=200, type=int)))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"lines": read_error_log_tail(limit)})


# ---------------------------------------------------------------------------
# vehicle identity
# ---------------------------------------------------------------------------
@app.route("/api/vehicle")
def api_vehicle():
    with app_state.state_lock:
        return jsonify(dict(app_state.vehicle_profile))


@app.route("/api/vehicle/refresh", methods=["POST"])
def api_vehicle_refresh():
    success, message = refresh_vin_profile()

    with app_state.state_lock:
        payload = dict(app_state.vehicle_profile)

    return jsonify({
        "success": success,
        "message": message,
        "vehicle_profile": payload
    }), (200 if success else 400)


@app.route("/api/vehicle/manual", methods=["POST"])
def api_vehicle_manual():
    payload = request.get_json(silent=True) or {}
    success, message = set_manual_vin(payload.get("vin", ""))

    with app_state.state_lock:
        profile_payload = dict(app_state.vehicle_profile)

    return jsonify({
        "success": success,
        "message": message,
        "vehicle_profile": profile_payload
    }), (200 if success else 400)


@app.route("/api/vehicle/plate", methods=["POST"])
def api_vehicle_plate():
    payload = request.get_json(silent=True) or {}
    success, message = refresh_plate_profile(payload.get("plate", ""))

    with app_state.state_lock:
        profile_payload = dict(app_state.vehicle_profile)

    return jsonify({
        "success": success,
        "message": message,
        "vehicle_profile": profile_payload
    }), (200 if success else 400)


# ---------------------------------------------------------------------------
# saved scans
# ---------------------------------------------------------------------------
@app.route("/api/scans")
def api_scans():
    try:
        return jsonify(get_recent_scans())
    except Exception as e:
        log_error("Read scans", e, affects_status=False)
        return jsonify([])


@app.route("/api/scans/save", methods=["POST"])
def api_scans_save():
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("label", "")).strip() or "Manual scan snapshot"

    try:
        saved = save_scan_snapshot(label, current_units(), get_active_vehicle_id())
        return jsonify({
            "success": True,
            "scan": saved,
            "scans": get_recent_scans()
        })
    except Exception as e:
        log_error("Save scan snapshot", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Could not save the scan snapshot."
        }), 500


@app.route("/api/scans/<int:scan_id>", methods=["DELETE"])
def api_scans_delete(scan_id):
    try:
        deleted = delete_scan_snapshot(scan_id)
        return jsonify({
            "success": deleted,
            "message": "Saved scan deleted." if deleted else "Saved scan not found.",
            "scans": get_recent_scans(),
        }), 200 if deleted else 404
    except Exception as e:
        log_error("Delete scan snapshot", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Saved scan could not be deleted."
        }), 500


@app.route("/api/scans/<int:scan_id>/share", methods=["POST"])
def api_scans_share(scan_id):
    try:
        if not scan_exists(scan_id):
            return jsonify({"success": False, "message": "Saved scan not found."}), 404

        token = create_or_get_share_link(scan_id)
        if not token:
            return jsonify({"success": False, "message": "Share link could not be created."}), 500

        return jsonify({
            "success": True,
            "share_token": token,
            "share_url": url_for("view_shared_report", token=token, _external=True),
        })
    except Exception as e:
        log_error("Share scan snapshot", e, affects_status=False)
        return jsonify({"success": False, "message": "Share link could not be created."}), 500


@app.route("/api/scans/<int:scan_id>/share", methods=["DELETE"])
def api_scans_unshare(scan_id):
    try:
        revoke_share_link(scan_id)
        return jsonify({"success": True})
    except Exception as e:
        log_error("Revoke shared scan", e, affects_status=False)
        return jsonify({"success": False, "message": "Share link could not be revoked."}), 500


@app.route("/share/<token>")
def view_shared_report(token):
    shared = get_shared_scan(token)
    if not shared:
        return "This shared report link is invalid or has been revoked.", 404
    return Response(render_export_html(shared["payload"]), mimetype="text/html")


# ---------------------------------------------------------------------------
# vehicles
# ---------------------------------------------------------------------------
@app.route("/api/vehicles")
def api_vehicles():
    try:
        return jsonify({"vehicles": get_vehicles(current_units()), "active_vehicle_id": get_active_vehicle_id()})
    except Exception as e:
        log_error("Read vehicles", e, affects_status=False)
        return jsonify({"vehicles": [], "active_vehicle_id": None})


@app.route("/api/vehicles", methods=["POST"])
def api_vehicles_save():
    payload = request.get_json(silent=True) or {}
    nickname = str(payload.get("nickname", "")).strip()
    plate = str(payload.get("plate", "")).strip()
    if not nickname:
        return jsonify({"success": False, "message": "Enter a nickname for the vehicle."}), 400
    if not plate:
        return jsonify({"success": False, "message": "Enter a license plate for the vehicle."}), 400

    try:
        vehicle = save_vehicle(
            nickname,
            payload.get("vin", ""),
            plate,
            payload.get("brand", ""),
            payload.get("model", ""),
            payload.get("year", ""),
            payload.get("tank"),
            current_units(),
        )
        return jsonify({
            "success": True,
            "vehicle": vehicle,
            "vehicles": get_vehicles(current_units()),
            "active_vehicle_id": get_active_vehicle_id(),
        })
    except Exception as e:
        log_error("Save vehicle", e, affects_status=False)
        return jsonify({"success": False, "message": "Vehicle could not be saved."}), 500


@app.route("/api/vehicles/<int:vehicle_id>", methods=["DELETE"])
def api_vehicles_delete(vehicle_id):
    try:
        deleted = delete_vehicle(vehicle_id)
        return jsonify({
            "success": deleted,
            "message": "Vehicle deleted." if deleted else "Vehicle not found.",
            "vehicles": get_vehicles(current_units()),
            "active_vehicle_id": get_active_vehicle_id(),
        }), 200 if deleted else 404
    except Exception as e:
        log_error("Delete vehicle", e, affects_status=False)
        return jsonify({"success": False, "message": "Vehicle could not be deleted."}), 500


@app.route("/api/vehicles/<int:vehicle_id>/activate", methods=["POST"])
def api_vehicles_activate(vehicle_id):
    try:
        set_active_vehicle_id(vehicle_id)
        return jsonify({"success": True, "active_vehicle": get_active_vehicle()})
    except Exception as e:
        log_error("Activate vehicle", e, affects_status=False)
        return jsonify({"success": False, "message": "Vehicle could not be activated."}), 500


@app.route("/api/vehicles/deactivate", methods=["POST"])
def api_vehicles_deactivate():
    try:
        set_active_vehicle_id(None)
        return jsonify({"success": True})
    except Exception as e:
        log_error("Deactivate vehicle", e, affects_status=False)
        return jsonify({"success": False, "message": "Could not clear the active vehicle."}), 500


# ---------------------------------------------------------------------------
# fuel log / trips / maintenance
# ---------------------------------------------------------------------------
@app.route("/api/fuel-logs")
def api_fuel_logs():
    try:
        vehicle_id = request.args.get("vehicle_id", type=int)
        return jsonify(get_fuel_log_history(current_units(), vehicle_id))
    except Exception as e:
        log_error("Read fuel logs", e, affects_status=False)
        return jsonify({"entries": [], "trend": []})


@app.route("/api/maintenance-reminder")
def api_maintenance_reminder():
    try:
        return jsonify(get_maintenance_reminder(current_units()))
    except Exception as e:
        log_error("Read maintenance reminder", e, affects_status=False)
        return jsonify({"available": False})


@app.route("/api/fuel-logs", methods=["POST"])
def api_fuel_logs_save():
    payload = request.get_json(silent=True) or {}
    try:
        mileage = float(payload.get("mileage"))
        liters = float(payload.get("liters"))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Enter a valid mileage and fuel amount."
        }), 400

    if mileage <= 0 or liters <= 0:
        return jsonify({
            "success": False,
            "message": "Mileage and fuel amount must be greater than zero."
        }), 400

    vehicle_id = payload.get("vehicle_id") or get_active_vehicle_id()
    try:
        save_fuel_log_entry(mileage, liters, payload.get("cost"), payload.get("notes", ""), current_units(), vehicle_id)
        return jsonify({
            "success": True,
            "fuel_log": get_fuel_log_history(current_units()),
        })
    except Exception as e:
        log_error("Save fuel log", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Fuel log entry could not be saved."
        }), 500


@app.route("/api/fuel-logs/<int:log_id>", methods=["DELETE"])
def api_fuel_logs_delete(log_id):
    try:
        deleted = delete_fuel_log_entry(log_id)
        return jsonify({
            "success": deleted,
            "message": "Fuel log entry deleted." if deleted else "Fuel log entry not found.",
            "fuel_log": get_fuel_log_history(current_units()),
        }), 200 if deleted else 404
    except Exception as e:
        log_error("Delete fuel log", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Fuel log entry could not be deleted."
        }), 500


@app.route("/api/trip-log")
def api_trip_log():
    try:
        vehicle_id = request.args.get("vehicle_id", type=int)
        return jsonify({"entries": get_trip_log_history(current_units(), vehicle_id)})
    except Exception as e:
        log_error("Read trip log", e, affects_status=False)
        return jsonify({"entries": []})


@app.route("/api/trip-log", methods=["POST"])
def api_trip_log_save():
    payload = request.get_json(silent=True) or {}
    try:
        start_mileage = float(payload.get("start_mileage"))
        end_mileage = float(payload.get("end_mileage"))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Enter a valid start and end odometer reading."
        }), 400

    if start_mileage < 0 or end_mileage < 0 or end_mileage == start_mileage:
        return jsonify({
            "success": False,
            "message": "The end odometer reading must be different from the start reading."
        }), 400

    vehicle_id = payload.get("vehicle_id") or get_active_vehicle_id()
    try:
        save_trip_log_entry(start_mileage, end_mileage, payload.get("purpose", ""), payload.get("notes", ""), current_units(), vehicle_id)
        return jsonify({
            "success": True,
            "trip_log": get_trip_log_history(current_units()),
        })
    except Exception as e:
        log_error("Save trip log", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Trip could not be saved."
        }), 500


@app.route("/api/trip-log/<int:trip_id>", methods=["DELETE"])
def api_trip_log_delete(trip_id):
    try:
        deleted = delete_trip_log_entry(trip_id)
        return jsonify({
            "success": deleted,
            "message": "Trip deleted." if deleted else "Trip not found.",
            "trip_log": get_trip_log_history(current_units()),
        }), 200 if deleted else 404
    except Exception as e:
        log_error("Delete trip log", e, affects_status=False)
        return jsonify({
            "success": False,
            "message": "Trip could not be deleted."
        }), 500


# ---------------------------------------------------------------------------
# car journal (garage notes)
# ---------------------------------------------------------------------------
@app.route("/api/garage-notes")
def api_garage_notes():
    try:
        vehicle_id = request.args.get("vehicle_id", type=int)
        notes = get_recent_garage_notes(vehicle_id=vehicle_id)
        notes = filter_garage_notes(
            notes,
            request.args.get("vin", ""),
            request.args.get("plate", ""),
            request.args.get("q", ""),
        )
        return jsonify(notes)
    except Exception as e:
        log_error("Read garage notes", e, affects_status=False)
        return jsonify([])


@app.route("/api/garage-notes/<int:note_id>")
def api_garage_note_detail(note_id):
    """One note including the scan snapshot captured with it."""
    note = get_garage_note(note_id)
    if not note:
        return jsonify({"success": False, "message": "Garage note not found."}), 404
    return jsonify({"success": True, "note": note})


@app.route("/api/garage-notes/export")
def api_garage_notes_export():
    try:
        vin = request.args.get("vin", "")
        plate = request.args.get("plate", "")
        query = request.args.get("q", "")
        notes = filter_garage_notes(get_recent_garage_notes(500), vin, plate, query)
        filename = f"car-journal-{time.strftime('%Y%m%d-%H%M%S')}.html"
        return Response(
            render_garage_notes_export_html(notes, APP_VERSION, vin, plate, query),
            mimetype="text/html",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        log_error("Export garage notes", e, affects_status=False)
        return "Car journal export could not be created.", 500


def _note_fields_from_request():
    """Garage-note fields from either a JSON body or a multipart form."""
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        return request.form, request.files.get("photo")
    return (request.get_json(silent=True) or {}), None


@app.route("/api/garage-notes", methods=["POST"])
def api_garage_notes_save():
    payload, photo = _note_fields_from_request()

    raw_vin = payload.get("vin") or ""
    raw_plate = payload.get("plate") or ""
    valid_identity, identity_message = validate_garage_note_identity(raw_vin, raw_plate)
    if not valid_identity:
        return jsonify({"success": False, "message": identity_message}), 400

    vin = normalize_vin(raw_vin) if raw_vin else ""
    plate = normalize_garage_plate(raw_plate)
    title = str(payload.get("title", "")).strip()
    mileage = str(payload.get("mileage", "")).strip()
    note = str(payload.get("note", "")).strip()
    vehicle_id = payload.get("vehicle_id") or get_active_vehicle_id()

    if not note:
        return jsonify({"success": False, "message": "Note text is required."}), 400

    # Photos are written to disk and referenced by generated name; nothing
    # about the uploaded file's own name or claimed type is trusted.
    attachment_name = None
    if photo is not None:
        attachment_name, error = save_attachment_from_upload(photo)
        if error:
            return jsonify({"success": False, "message": error}), 400
    elif payload.get("attachment"):
        legacy = payload.get("attachment")
        data_uri = legacy.get("data") if isinstance(legacy, dict) else legacy
        attachment_name, error = save_attachment_from_data_uri(data_uri)
        if error:
            return jsonify({"success": False, "message": error}), 400

    try:
        saved = save_garage_note_snapshot(
            vin, plate, title, mileage, note, attachment_name, current_units(), vehicle_id
        )
        return jsonify({
            "success": True,
            "note": saved,
            "notes": get_recent_garage_notes(),
        })
    except Exception as e:
        log_error("Save garage note", e, affects_status=False)
        return jsonify({"success": False, "message": "Garage note could not be saved."}), 500


@app.route("/api/garage-notes/<int:note_id>", methods=["DELETE"])
def api_garage_notes_delete(note_id):
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "YES":
        return jsonify({"success": False, "message": "Confirmation is missing."}), 400

    try:
        deleted = delete_garage_note(note_id)
        return jsonify({
            "success": deleted,
            "message": "Garage note deleted." if deleted else "Garage note not found.",
            "notes": get_recent_garage_notes(),
        }), 200 if deleted else 404
    except Exception as e:
        log_error("Delete garage note", e, affects_status=False)
        return jsonify({"success": False, "message": "Garage note could not be deleted."}), 500


@app.route("/api/garage-notes/<int:note_id>", methods=["PUT"])
def api_garage_notes_update(note_id):
    payload = request.get_json(silent=True) or {}
    raw_vin = payload.get("vin") or ""
    raw_plate = payload.get("plate") or ""
    valid_identity, identity_message = validate_garage_note_identity(raw_vin, raw_plate)
    if not valid_identity:
        return jsonify({"success": False, "message": identity_message}), 400

    title = str(payload.get("title", "")).strip()
    mileage = str(payload.get("mileage", "")).strip()
    note = str(payload.get("note", "")).strip()

    if not note:
        return jsonify({"success": False, "message": "Note text is required."}), 400

    try:
        updated = update_garage_note(note_id, raw_vin, raw_plate, title, mileage, note)
        return jsonify({
            "success": updated,
            "message": "Garage note updated." if updated else "Garage note not found.",
            "notes": get_recent_garage_notes(),
        }), 200 if updated else 404
    except Exception as e:
        log_error("Update garage note", e, affects_status=False)
        return jsonify({"success": False, "message": "Garage note could not be updated."}), 500


@app.route("/uploads/<name>")
def serve_upload(name):
    """Serve a stored garage-note photo.

    Only names this app generated are accepted, so the path can never be
    steered outside the uploads folder.
    """
    if not is_valid_attachment_name(name):
        return "Not found", 404
    return send_from_directory(app_state.UPLOAD_DIR, name, max_age=86400)


# ---------------------------------------------------------------------------
# connection control
# ---------------------------------------------------------------------------
@app.route("/api/reconnect", methods=["POST"])
def reconnect_obd():
    try:
        with app_state.state_lock:
            app_state.obd_status["connected"] = False
            app_state.obd_status["protocol"] = "Unknown"
            app_state.obd_status["error"] = "Manual reconnect requested."
            app_state.obd_status["user_message"] = "Manual reconnect started. Checking adapter..."
            app_state.obd_status["connecting"] = True
            app_state.obd_status["last_update"] = time.strftime("%H:%M:%S")
            app_state.vehicle_data = {}

        # A manual reconnect is an explicit "try again now", so the backoff
        # that may have built up is cleared first.
        app_state.connect_failure_streak = 0
        invalidate_port_cache()
        reset_vehicle_profile()
        reset_readiness_state()
        reset_dtc_state("Reconnect started. Run a new fault code scan after the adapter is back online.")
        with app_state.obd_lock:
            try:
                if app_state.connection:
                    app_state.connection.close()
            except Exception as e:
                log_error("Close OBD connection", e)
            finally:
                app_state.connection = None

        connect_obd()

        with app_state.state_lock:
            return jsonify({
                "success": True,
                "message": app_state.obd_status["user_message"],
                "status": dict(app_state.obd_status)
            })
    except Exception as e:
        log_error("Reconnect OBD", e)
        return jsonify({
            "success": False,
            "message": friendly_message(e, source="Reconnect OBD")
        }), 500


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return jsonify({
            "success": False,
            "message": error.description
        }), error.code

    # The raw exception text can carry file paths and internals, so it goes
    # to the log rather than to the browser.
    log_error("Flask route", error, affects_status=False)
    return jsonify({
        "success": False,
        "message": "The scanner hit an unexpected error. Check the System page error log.",
    }), 500


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------
_startup_done = threading.Event()


def start_scanner():
    """Prepare storage and start the background threads exactly once.

    This runs on import as well as from __main__, so the scanner also works
    under `flask run`, waitress or any other WSGI server - not only when
    app.py is executed directly.
    """
    if _startup_done.is_set():
        return
    # Under the Flask debug reloader the parent process would otherwise
    # start a second set of threads and a second serial connection.
    if DEBUG and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    _startup_done.set()

    init_config_db()
    ensure_upload_dir()
    app_state.LOG_DIR.mkdir(parents=True, exist_ok=True)
    connect_obd()
    start_background_threads()


start_scanner()


if __name__ == "__main__":
    start_scanner()
    if DEBUG:
        print("WARNING: debug mode is on. Do not use this on a shared network.")
    print(f"Car OBD Diagnostics {APP_VERSION} - http://{SERVER_HOST}:{SERVER_PORT}")
    app.run(
        host=SERVER_HOST,
        port=SERVER_PORT,
        debug=DEBUG,
        threaded=True,
    )
