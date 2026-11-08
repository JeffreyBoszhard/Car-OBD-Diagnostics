"""Fault-code reading and the full multi-step vehicle diagnostic scan
(stored/pending/permanent DTCs, freeze frame, readiness, Mode 06, VIN,
odometer/DPF/battery and supported-PID steps) with live progress tracking on
vehicle_scan_state.

Note on locking: every OBD read here happens outside app_state.state_lock.
Reading a freeze frame means thirteen serial round trips, and holding the
shared state lock across those blocked every API request and both polling
loops for as long as the adapter took to answer. The lock is taken only to
publish the finished result.
"""
from scanner_core import app_state
from scanner_core.app_state import VEHICLE_SCAN_PART_DEFS, now_time
from scanner_core.demo_services import (
    build_demo_dtc_snapshot,
    build_demo_freeze_frame,
    build_demo_readiness,
    normalize_demo_preset,
)
from scanner_core.dtc_catalog import enrich_dtc
from scanner_core.error_services import friendly_message, log_error
from scanner_core.extended_pid_services import refresh_extended_data
from scanner_core.health_services import build_mode06_snapshot, get_supported_sensor_matrix
from scanner_core.obd_bridge import get_command
from scanner_core.obd_services import get_freeze_frame_snapshot, get_readiness_snapshot
from scanner_core.settings_services import get_demo_mode_enabled, get_demo_preset_name
from scanner_core.vin_services import refresh_vin_profile

def reset_readiness_state():

    with app_state.state_lock:
        app_state.readiness_data = {
            "available": False,
            "mil": None,
            "dtc_count": None,
            "ignition_type": "",
            "monitors": [],
        }


def reset_dtc_state(message="No fault code scan run yet."):

    with app_state.state_lock:
        app_state.dtc_data = {
            "stored": [],
            "pending": [],
            "permanent": []
        }
        app_state.dtc_status["has_scan"] = False
        app_state.dtc_status["scanning"] = False
        app_state.dtc_status["last_scan"] = None
        app_state.dtc_status["message"] = message
        app_state.freeze_frame_data = {
            "available": False,
            "values": {},
        }


def build_demo_readiness_for_current_clear_state(preset, cleared=None):
    readiness = build_demo_readiness(preset)

    if cleared is None:
        with app_state.state_lock:
            cleared = bool(app_state.demo_codes_cleared)

    if cleared:
        readiness = dict(readiness)
        readiness["mil"] = False
        readiness["dtc_count"] = 0

    return readiness


def read_dtc(command):
    if command is None:
        return []

    if not app_state.connection or not app_state.connection.is_connected():
        raise RuntimeError("No OBD connection.")

    try:
        with app_state.obd_lock:
            response = app_state.connection.query(command)

        if response.is_null() or not response.value:
            return []

        results = []

        for code, description in response.value:
            results.append(enrich_dtc(code, description))

        return results

    except Exception as e:
        raise RuntimeError(f"DTC query failed: {e}") from e


def scan_dtc_codes():

    if get_demo_mode_enabled():
        with app_state.state_lock:
            demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
            cleared = bool(app_state.demo_codes_cleared)

        demo_dtc = (
            {
                "stored": [],
                "pending": [],
                "permanent": [],
                "message": "Demo fault codes are cleared. Change demo preset to load demo faults again.",
            }
            if cleared
            else build_demo_dtc_snapshot(demo_preset)
        )
        new_dtc_data = {
            "stored": list(demo_dtc["stored"]),
            "pending": list(demo_dtc["pending"]),
            "permanent": list(demo_dtc["permanent"]),
        }
        with app_state.state_lock:
            app_state.dtc_data = new_dtc_data
            app_state.freeze_frame_data = {"available": False, "values": {}} if cleared else build_demo_freeze_frame(demo_preset)
            app_state.dtc_status["has_scan"] = True
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["last_scan"] = now_time()
            app_state.dtc_status["message"] = demo_dtc["message"]
        return new_dtc_data

    if not app_state.connection or not app_state.connection.is_connected():
        raise RuntimeError("No OBD connection.")

    with app_state.state_lock:
        app_state.dtc_status["scanning"] = True
        app_state.dtc_status["message"] = "Scanning fault codes..."
        app_state.dtc_status["last_scan"] = now_time()

    try:
        new_dtc_data = {
            "stored": read_dtc(get_command("GET_DTC")),
            "pending": read_dtc(get_command("GET_CURRENT_DTC")),
            "permanent": read_dtc(get_command("GET_PERMANENT_DTC"))
        }
        total = (
            len(new_dtc_data["stored"])
            + len(new_dtc_data["pending"])
            + len(new_dtc_data["permanent"])
        )

        # Read the freeze frame BEFORE taking the state lock: this is a
        # series of slow serial queries and nothing else may be blocked on
        # the shared state while it runs.
        freeze_frame = get_freeze_frame_snapshot(app_state.connection, app_state.obd_lock, get_command)

        with app_state.state_lock:
            app_state.dtc_data = new_dtc_data
            app_state.freeze_frame_data = freeze_frame
            app_state.dtc_status["has_scan"] = True
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["last_scan"] = now_time()
            app_state.dtc_status["message"] = (
                f"Scan completed. {total} code(s) found."
                if total
                else "Scan completed. No fault codes found."
            )

        return new_dtc_data
    except Exception:
        with app_state.state_lock:
            app_state.dtc_status["has_scan"] = False
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["last_scan"] = now_time()
            app_state.dtc_status["message"] = "Fault code scan failed."
        raise


def _vehicle_scan_set_part(part_id, **fields):
    with app_state.vehicle_scan_lock:
        for index, part in enumerate(app_state.vehicle_scan_state["parts"]):
            if part["id"] == part_id:
                part.update(fields)
                if fields.get("status") == "scanning":
                    app_state.vehicle_scan_state["current_index"] = index
                break


def _vehicle_scan_cancelled():
    with app_state.vehicle_scan_lock:
        return app_state.vehicle_scan_state["cancelled"]


def _vehicle_still_connected(demo_mode):
    if demo_mode:
        return True
    return bool(app_state.connection and app_state.connection.is_connected())


def _run_vehicle_scan_stored_dtc(demo_mode):
    if demo_mode:
        data = scan_dtc_codes()
        stored = data["stored"]
    else:
        stored = read_dtc(get_command("GET_DTC"))
        with app_state.state_lock:
            app_state.dtc_data["stored"] = stored
    if stored:
        return "faults_found", f"{len(stored)} fault code(s) found."
    return "no_faults", "No fault codes found."


def _run_vehicle_scan_pending_dtc(demo_mode):
    if demo_mode:
        with app_state.state_lock:
            pending = list(app_state.dtc_data["pending"])
    else:
        pending = read_dtc(get_command("GET_CURRENT_DTC"))
        with app_state.state_lock:
            app_state.dtc_data["pending"] = pending
    if pending:
        return "faults_found", f"{len(pending)} fault code(s) found."
    return "no_faults", "No fault codes found."


def _run_vehicle_scan_permanent_dtc(demo_mode):
    if demo_mode:
        with app_state.state_lock:
            permanent = list(app_state.dtc_data["permanent"])
    else:
        permanent = read_dtc(get_command("GET_PERMANENT_DTC"))
        with app_state.state_lock:
            app_state.dtc_data["permanent"] = permanent
    if permanent:
        return "faults_found", f"{len(permanent)} fault code(s) found."
    return "no_faults", "No fault codes found."


def _run_vehicle_scan_freeze_frame(demo_mode):
    if demo_mode:
        with app_state.state_lock:
            demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
            cleared = bool(app_state.demo_codes_cleared)
        snapshot = {"available": False, "values": {}} if cleared else build_demo_freeze_frame(demo_preset)
        with app_state.state_lock:
            app_state.freeze_frame_data = snapshot
    else:
        snapshot = get_freeze_frame_snapshot(app_state.connection, app_state.obd_lock, get_command)
        with app_state.state_lock:
            app_state.freeze_frame_data = snapshot
    if snapshot.get("available"):
        return "complete", "Freeze frame snapshot available."
    return "no_data", "No freeze frame data available."


def _run_vehicle_scan_readiness(demo_mode):
    if demo_mode:
        with app_state.state_lock:
            demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
            cleared = bool(app_state.demo_codes_cleared)
        snapshot = build_demo_readiness_for_current_clear_state(demo_preset, cleared)
        with app_state.state_lock:
            app_state.readiness_data = snapshot
    else:
        snapshot = get_readiness_snapshot(app_state.connection, app_state.obd_lock, get_command)
        with app_state.state_lock:
            app_state.readiness_data = snapshot
    if not snapshot.get("available"):
        return "not_supported", "Readiness monitors not supported by this ECU."
    monitors = snapshot.get("monitors") or []
    incomplete = [m for m in monitors if not m.get("complete")]
    if incomplete:
        return "warning", f"{len(monitors) - len(incomplete)} ready / {len(incomplete)} incomplete."
    return "complete", f"{len(monitors)} monitor(s) ready." if monitors else "No monitor data reported."


def _run_vehicle_scan_mode06(demo_mode):
    # force=True: a scan the user explicitly asked for must read the adapter
    # again rather than reuse the cached snapshot.
    snapshot = build_mode06_snapshot(force=True)
    if not snapshot.get("available"):
        return "not_supported", "Mode 06 test results not available from this adapter/vehicle."
    return "complete", f"{len(snapshot.get('tests') or [])} monitor test result(s) read."


def _run_vehicle_scan_vehicle_info(demo_mode):
    ok, message = refresh_vin_profile()
    with app_state.state_lock:
        vin = app_state.vehicle_profile.get("vin", "")
    if ok and vin:
        return "complete", f"VIN: {vin}"
    if ok:
        return "no_data", message
    return "error", message


def _run_vehicle_scan_extended_pids(demo_mode):
    """Odometer, DPF and hybrid/EV battery pack, where the car answers."""
    data = refresh_extended_data(demo_mode=demo_mode)
    parts = []

    odometer = data.get("odometer") or {}
    if odometer.get("km") is not None:
        parts.append(f"Odometer {odometer['km']:.0f} km")

    dpf = data.get("dpf") or {}
    if dpf.get("available"):
        temperature = dpf.get("temperature_c")
        parts.append(f"DPF {temperature} C" if temperature is not None else "DPF data present")

    battery = data.get("battery_pack") or {}
    if battery.get("available"):
        parts.append(f"battery pack {battery.get('remaining_pct')}%")

    if not parts:
        return "not_supported", "This vehicle does not expose odometer, DPF or battery-pack PIDs."
    # Joined as-is: capitalising the whole sentence would turn "DPF" into
    # "dpf" and "82.0%" into part of a lowercased run.
    return "complete", ", ".join(parts) + "."


def _run_vehicle_scan_supported_pids(demo_mode):
    matrix = get_supported_sensor_matrix()
    supported = [s for s in matrix if s.get("supported")]
    if not matrix:
        return "no_data", "Could not read the supported PID list."
    return "complete", f"{len(supported)} of {len(matrix)} live data PIDs supported."


def _build_vehicle_scan_summary():
    with app_state.vehicle_scan_lock:
        parts = {part["id"]: part for part in app_state.vehicle_scan_state["parts"]}

    def count_from(part_id):
        part = parts.get(part_id) or {}
        result = part.get("result") or ""
        return result

    with app_state.state_lock:
        stored = len(app_state.dtc_data["stored"])
        pending = len(app_state.dtc_data["pending"])
        permanent = len(app_state.dtc_data["permanent"])
        vin = app_state.vehicle_profile.get("vin", "")
        readiness_snapshot = dict(app_state.readiness_data)
        freeze_snapshot = dict(app_state.freeze_frame_data)

    monitors = readiness_snapshot.get("monitors") or []
    incomplete = len([m for m in monitors if not m.get("complete")])
    ready = len(monitors) - incomplete
    mode06 = build_mode06_snapshot()

    attention = bool(stored or pending or permanent or incomplete)

    return {
        "vin": vin,
        "ecus_responding": 1 if _vehicle_still_connected(get_demo_mode_enabled()) else 0,
        "stored_dtc_count": stored,
        "pending_dtc_count": pending,
        "permanent_dtc_count": permanent,
        "readiness_ready": ready,
        "readiness_incomplete": incomplete,
        "freeze_frame_available": bool(freeze_snapshot.get("available")),
        "mode06_tests": len(mode06.get("tests") or []),
        "overall_status": "attention_required" if attention else "all_clear",
    }


VEHICLE_SCAN_RUNNERS = {
    "stored_dtc": _run_vehicle_scan_stored_dtc,
    "pending_dtc": _run_vehicle_scan_pending_dtc,
    "permanent_dtc": _run_vehicle_scan_permanent_dtc,
    "freeze_frame": _run_vehicle_scan_freeze_frame,
    "readiness": _run_vehicle_scan_readiness,
    "mode06": _run_vehicle_scan_mode06,
    "vehicle_info": _run_vehicle_scan_vehicle_info,
    "extended_pids": _run_vehicle_scan_extended_pids,
    "supported_pids": _run_vehicle_scan_supported_pids,
}


def run_full_vehicle_scan():
    demo_mode = get_demo_mode_enabled()

    with app_state.vehicle_scan_lock:
        app_state.vehicle_scan_state["active"] = True
        app_state.vehicle_scan_state["cancelled"] = False
        app_state.vehicle_scan_state["interrupted"] = False
        app_state.vehicle_scan_state["started_at"] = now_time()
        app_state.vehicle_scan_state["finished_at"] = None
        app_state.vehicle_scan_state["current_index"] = -1
        app_state.vehicle_scan_state["summary"] = None
        app_state.vehicle_scan_state["connection_error"] = None
        app_state.vehicle_scan_state["parts"] = [
            {"id": part_id, "name": name, "status": "waiting", "detail": "", "result": None}
            for part_id, name in VEHICLE_SCAN_PART_DEFS
        ]

    with app_state.state_lock:
        app_state.dtc_status["scanning"] = True
        app_state.dtc_status["message"] = "Vehicle diagnostic scan in progress..."
        app_state.dtc_status["last_scan"] = now_time()

    try:
        if not demo_mode and not (app_state.connection and app_state.connection.is_connected()):
            with app_state.vehicle_scan_lock:
                app_state.vehicle_scan_state["interrupted"] = True
                app_state.vehicle_scan_state["connection_error"] = (
                    "No OBD connection. Connect the adapter to the vehicle before starting a scan."
                )
            with app_state.state_lock:
                app_state.dtc_status["message"] = "Fault code scan failed: no OBD connection."
            return

        for part_id, name in VEHICLE_SCAN_PART_DEFS:
            if _vehicle_scan_cancelled():
                _vehicle_scan_set_part(part_id, status="cancelled", result="Scan cancelled.")
                continue

            with app_state.vehicle_scan_lock:
                already_interrupted = app_state.vehicle_scan_state["interrupted"]

            if already_interrupted:
                _vehicle_scan_set_part(part_id, status="cancelled", result="Not scanned - connection lost.")
                continue

            if not _vehicle_still_connected(demo_mode):
                _vehicle_scan_set_part(part_id, status="cancelled", result="Not scanned - connection lost.")
                with app_state.vehicle_scan_lock:
                    app_state.vehicle_scan_state["interrupted"] = True
                    app_state.vehicle_scan_state["connection_error"] = "Vehicle communication was lost during the scan."
                continue

            _vehicle_scan_set_part(part_id, status="scanning", detail=f"Scanning {name}...")

            try:
                status, result = VEHICLE_SCAN_RUNNERS[part_id](demo_mode)
            except Exception as e:
                log_error(f"Vehicle scan: {name}", e)
                status, result = "error", friendly_message(e, source=name)

            _vehicle_scan_set_part(part_id, status=status, result=result, detail="")

        with app_state.state_lock:
            app_state.dtc_status["has_scan"] = True
            app_state.dtc_status["scanning"] = False
            app_state.dtc_status["last_scan"] = now_time()
            app_state.dtc_status["message"] = "Vehicle diagnostic scan complete."

        summary = _build_vehicle_scan_summary()
        with app_state.vehicle_scan_lock:
            app_state.vehicle_scan_state["summary"] = summary
    finally:
        with app_state.vehicle_scan_lock:
            app_state.vehicle_scan_state["active"] = False
            app_state.vehicle_scan_state["finished_at"] = now_time()
        with app_state.state_lock:
            app_state.dtc_status["scanning"] = False
