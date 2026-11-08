"""Derived diagnostic reports built from the current live vehicle state:
the purchase-style health checklist, battery/charging check, supported PID
matrix and simple pass/fail summary shown on the report page.

Warning limits are not hardcoded here. They come from the warning_thresholds
block in dashboard_runtime.json (editable from the System page), so the
numbers the checklist judges by are the same ones the live sensor rows
highlight with.

The supported-PID matrix and the Mode 06 snapshot are cached. Both of them
talk to the adapter, and both used to be rebuilt for every single live-data
request - which meant the dashboard's own refresh rate was stealing serial
bandwidth from the gauges.
"""
import threading
import time

from config import MODE06_CACHE_TTL, PID_SUPPORT_CACHE_TTL
from scanner_core import app_state
from scanner_core.connection_services import safe_query
from scanner_core.error_services import log_error
from scanner_core.live_data_services import LIVE_COMMANDS
from scanner_core.obd_bridge import get_command
from scanner_core.obd_services import list_serial_ports, ports_match
from scanner_core.settings_services import get_configured_port, get_demo_mode_enabled
from scanner_core.setup_services import threshold
from scanner_core.units_services import format_detail_number, number_from_value

_cache_lock = threading.Lock()
_pid_support_cache = {"value": None, "read_at": 0.0, "connected": False}
_mode06_cache = {"value": None, "read_at": 0.0, "connected": False}


def build_health_report(units="metric"):
    with app_state.state_lock:
        status = dict(app_state.obd_status)
        vehicle = dict(app_state.vehicle_data)
        dtc = {
            "stored": list(app_state.dtc_data["stored"]),
            "pending": list(app_state.dtc_data["pending"]),
            "permanent": list(app_state.dtc_data["permanent"])
        }
        profile = dict(app_state.vehicle_profile)
        connection_hint = dict(app_state.obd_status.get("connection_hint") or {})

    stored_count = len(dtc["stored"])
    pending_count = len(dtc["pending"])
    permanent_count = len(dtc["permanent"])
    rpm = number_from_value(vehicle.get("rpm", {}).get("value"))
    control_voltage = number_from_value(vehicle.get("control_voltage", {}).get("value"))
    coolant_temp = number_from_value(vehicle.get("coolant_temp", {}).get("value"))
    fuel_trim = number_from_value(vehicle.get("long_fuel_trim_1", {}).get("value"))
    warmups_since_clear = number_from_value(vehicle.get("warmups_since_clear", {}).get("value"))
    distance_since_clear = number_from_value(vehicle.get("distance_since_clear", {}).get("value"))
    time_since_clear = number_from_value(vehicle.get("time_since_clear", {}).get("value"))

    voltage_low = threshold("ecu_voltage_low_v", 11.8)
    coolant_limit = threshold("coolant_temp_c", 100)
    fuel_trim_limit = threshold("fuel_trim_abs_pct", 12)

    checklist = []
    score = 100
    status_level = "good"
    headline = "Looks healthy so far."

    if not status.get("connected"):
        checklist.append({
            "level": "warning",
            "title": "Scanner not connected",
            "detail": "No live vehicle connection yet, so results are incomplete."
        })
        score = 0
        status_level = "warning"
        headline = "No live vehicle data yet."

        if connection_hint.get("kind") == "ignition_likely_off":
            checklist.append({
                "level": "info",
                "title": "Ignition is likely off",
                "detail": connection_hint.get("detail") or "Turn the ignition on so the ECU can respond."
            })

    if stored_count > 0:
        checklist.append({
            "level": "danger",
            "title": f"{stored_count} stored fault code(s)",
            "detail": "Stored DTCs usually deserve follow-up before buying."
        })
        score -= min(40, stored_count * 12)

    if pending_count > 0:
        checklist.append({
            "level": "warning",
            "title": f"{pending_count} pending fault code(s)",
            "detail": "Pending codes can point to intermittent or recently detected issues."
        })
        score -= min(20, pending_count * 6)

    if permanent_count > 0:
        checklist.append({
            "level": "warning",
            "title": f"{permanent_count} permanent fault code(s)",
            "detail": "Permanent codes may stay after repairs until drive cycles complete."
        })
        score -= min(15, permanent_count * 4)

    if control_voltage is not None and control_voltage < voltage_low:
        checklist.append({
            "level": "warning",
            "title": "Low ECU voltage",
            "detail": f"Voltage looks low at about {control_voltage:.1f} V. Battery or charging system may need attention."
        })
        score -= 10

    if fuel_trim is not None and abs(fuel_trim) >= fuel_trim_limit:
        checklist.append({
            "level": "warning",
            "title": "Fuel trim looks high",
            "detail": f"Long fuel trim is around {fuel_trim:.1f}. Could hint at air/fuel imbalance."
        })
        score -= 8

    if coolant_temp is not None and coolant_temp > coolant_limit:
        checklist.append({
            "level": "danger",
            "title": "Coolant temperature is high",
            "detail": f"Coolant is around {format_detail_number(coolant_temp, 'temperature', units)}. Check cooling system health."
        })
        score -= 20

    if rpm is None:
        checklist.append({
            "level": "info",
            "title": "No RPM data yet",
            "detail": "Turn ignition on and start the engine to get a better health picture."
        })

    if not profile.get("vin"):
        checklist.append({
            "level": "info",
            "title": "VIN not read yet",
            "detail": "Some cars do not expose VIN over standard OBD, but reading it helps confirm identity."
        })

    checklist.append({
        "level": "info",
        "title": "Standard OBD only",
        "detail": "Engine and emission data are read over standard OBD-II. ABS, airbag and body modules may need a brand-specific scanner."
    })

    possible_recent_clear = status.get("connected") and (
        (warmups_since_clear is not None and warmups_since_clear <= 3)
        or (distance_since_clear is not None and distance_since_clear <= 50)
        or (time_since_clear is not None and time_since_clear <= 120)
    )

    if possible_recent_clear:
        details = []
        if warmups_since_clear is not None:
            details.append(f"{warmups_since_clear:.0f} warm-up cycle(s)")
        if distance_since_clear is not None:
            details.append(format_detail_number(distance_since_clear, "distance", units))
        if time_since_clear is not None:
            details.append(f"{time_since_clear:.0f} minute(s)")

        checklist.append({
            "level": "warning",
            "title": "Codes may have been cleared recently",
            "detail": (
                "ECU counters since DTC clear look low"
                + (f" ({', '.join(details)})." if details else ".")
                + " This can hide faults that have not returned yet."
            )
        })
        score -= 18

    score = max(0, min(100, score))

    if status.get("connected"):
        if stored_count > 0 or (control_voltage is not None and control_voltage < voltage_low - 0.4) or (coolant_temp is not None and coolant_temp > coolant_limit + 4):
            status_level = "danger"
            headline = "Possible red flags detected."
        elif pending_count > 0 or permanent_count > 0 or (fuel_trim is not None and abs(fuel_trim) >= fuel_trim_limit) or possible_recent_clear:
            status_level = "warning"
            headline = "A few things need checking."

    return {
        "score": score,
        "status": status_level,
        "headline": headline,
        "counts": {
            "stored": stored_count,
            "pending": pending_count,
            "permanent": permanent_count
        },
        "checklist": checklist
    }


def get_supported_sensor_matrix():
    if get_demo_mode_enabled():
        sensors = []
        for key, (label, command) in LIVE_COMMANDS.items():
            sensors.append({
                "key": key,
                "label": label,
                "command": command.name if command else key.upper(),
                "supported": True
            })
        sensors.sort(key=lambda item: item["label"].lower())
        return sensors

    supported_names = set()

    try:
        if app_state.connection and app_state.connection.is_connected():
            with app_state.obd_lock:
                supported_names = {command.name for command in app_state.connection.supported_commands}
    except Exception as e:
        log_error("Read supported commands", e)

    sensors = []

    for key, (label, command) in LIVE_COMMANDS.items():
        command_name = command.name if command else "Unavailable"
        supported = bool(command and command_name in supported_names)
        sensors.append({
            "key": key,
            "label": label,
            "command": command_name,
            "supported": supported
        })

    sensors.sort(key=lambda item: (not item["supported"], item["label"].lower()))
    return sensors


def build_battery_check():
    with app_state.state_lock:
        vehicle = dict(app_state.vehicle_data)
        status = dict(app_state.obd_status)

    ecu_voltage = number_from_value(vehicle.get("control_voltage", {}).get("value"))
    adapter_voltage = number_from_value(vehicle.get("voltage", {}).get("value"))
    rpm = number_from_value(vehicle.get("rpm", {}).get("value"))
    voltage = ecu_voltage if ecu_voltage is not None else adapter_voltage

    result = {
        "available": voltage is not None,
        "status": "unknown",
        "headline": "Battery check unavailable",
        "detail": "ECU or adapter voltage is not available yet.",
        "voltage": voltage,
        "ecu_voltage": ecu_voltage,
        "adapter_voltage": adapter_voltage,
        "running": bool(rpm is not None and rpm > 450),
    }

    if voltage is None:
        return result

    voltage_low = threshold("ecu_voltage_low_v", 11.8)
    voltage_high = threshold("ecu_voltage_high_v", 15.0)

    if result["running"]:
        if voltage < 13.2:
            result.update({
                "status": "warning",
                "headline": "Charging voltage looks low",
                "detail": f"Voltage is about {voltage:.1f} V while the engine appears to be running. Check alternator, battery and grounds.",
            })
        elif voltage > voltage_high:
            result.update({
                "status": "warning",
                "headline": "Charging voltage looks high",
                "detail": f"Voltage is about {voltage:.1f} V. Check alternator regulator and battery condition.",
            })
        else:
            result.update({
                "status": "good",
                "headline": "Charging voltage looks normal",
                "detail": f"Voltage is about {voltage:.1f} V with the engine running.",
            })
    else:
        if voltage < voltage_low:
            result.update({
                "status": "warning",
                "headline": "Battery voltage looks low",
                "detail": f"Voltage is about {voltage:.1f} V. Charge or test the battery before deeper diagnostics.",
            })
        elif voltage < 12.2:
            result.update({
                "status": "info",
                "headline": "Battery voltage is a little low",
                "detail": f"Voltage is about {voltage:.1f} V with no clear running RPM signal.",
            })
        else:
            result.update({
                "status": "good",
                "headline": "Battery voltage looks usable",
                "detail": f"Voltage is about {voltage:.1f} V with no clear running RPM signal.",
            })

    if status.get("demo_mode"):
        result["detail"] += " Demo mode values are simulated."

    return result


def build_simple_summary(payload):
    health = payload.get("health", {})
    battery = payload.get("battery_check", {})
    dtc = payload.get("dtc", {})
    readiness = payload.get("readiness", {})
    status = payload.get("status", {})

    stored = len(dtc.get("stored", []))
    pending = len(dtc.get("pending", []))
    permanent = len(dtc.get("permanent", []))
    incomplete = [
        item.get("name", "")
        for item in readiness.get("monitors", [])
        if item.get("available") and not item.get("complete")
    ]

    items = []
    level = "good"
    headline = "No obvious red flags right now."

    if not status.get("connected"):
        level = "warning"
        headline = "Connect the scanner for a real result."
        items.append("No live ECU connection is active yet.")

    if stored or pending or permanent:
        level = "danger" if stored else "warning"
        headline = "Fault codes need attention."
        items.append(f"Codes found: stored {stored}, pending {pending}, permanent {permanent}.")

    if battery.get("status") in {"warning", "danger"}:
        level = "warning" if level == "good" else level
        items.append(battery.get("headline", "Battery or charging system needs checking."))

    if incomplete:
        level = "warning" if level == "good" else level
        items.append(f"Readiness incomplete: {', '.join(incomplete[:4])}.")

    if health.get("status") == "danger":
        level = "danger"
        headline = health.get("headline", headline)
    elif health.get("status") == "warning" and level == "good":
        level = "warning"
        headline = health.get("headline", "A few things need checking.")

    if not items:
        items.append("No stored fault codes, critical voltage warning or readiness warning is visible in the current snapshot.")

    return {
        "enabled_by_default": False,
        "level": level,
        "headline": headline,
        "items": items,
    }


def build_pid_support_summary(force=False):
    """Supported/unsupported live-data PIDs, cached.

    Reading the vehicle's supported-PID bitmaps means talking to the ECU, so
    the answer is reused for PID_SUPPORT_CACHE_TTL seconds. The cache is
    keyed on the connection state too, so plugging in or unplugging the
    adapter refreshes it immediately rather than after the TTL.
    """
    connected = bool(app_state.connection and getattr(app_state.connection, "is_connected", bool)())
    now = time.monotonic()

    with _cache_lock:
        cached = _pid_support_cache
        if (
            not force
            and cached["value"] is not None
            and cached["connected"] == connected
            and (now - cached["read_at"]) < PID_SUPPORT_CACHE_TTL
        ):
            return cached["value"]

    summary = _build_pid_support_summary_uncached()

    with _cache_lock:
        _pid_support_cache["value"] = summary
        _pid_support_cache["read_at"] = now
        _pid_support_cache["connected"] = connected
    return summary


def _build_pid_support_summary_uncached():
    sensors = get_supported_sensor_matrix()
    supported = [item for item in sensors if item["supported"]]
    unsupported = [item for item in sensors if not item["supported"]]
    return {
        "total": len(sensors),
        "supported_count": len(supported),
        "unsupported_count": len(unsupported),
        "supported": supported,
        "unsupported": unsupported,
    }


def enrich_connection_quality(status, connection_quality):
    quality = dict(connection_quality or {})
    if status.get("demo_mode"):
        quality["quality"] = {
            "level": "good",
            "label": "Demo mode",
            "detail": "Simulated adapter and ECU are online.",
        }
        return quality

    detected_ports = list_serial_ports()
    selected_port = str(status.get("current_port") or get_configured_port() or "").strip()
    selected_port_present = any(
        ports_match(item.get("device"), selected_port) for item in detected_ports
    )
    any_usb_serial_present = bool(detected_ports)

    if selected_port_present or any_usb_serial_present:
        quality["adapter_connected"] = True
        quality["selected_port_present"] = bool(selected_port_present)
        quality["detected_ports"] = detected_ports
        if not quality.get("phase") or str(quality.get("phase")).lower() == "not connected":
            quality["phase"] = "USB Adapter Detected"

    if quality.get("live_data_active"):
        summary = {
            "level": "good",
            "label": "Live ECU data",
            "detail": "Standard OBD live data is available.",
        }
    elif quality.get("car_connected"):
        summary = {
            "level": "good",
            "label": "ECU responding",
            "detail": "The ECU is responding on standard OBD.",
        }
    elif quality.get("port_powered"):
        summary = {
            "level": "warning",
            "label": "OBD port detected",
            "detail": "The adapter sees the vehicle bus. Waiting for ECU response.",
        }
    elif quality.get("adapter_connected"):
        summary = {
            "level": "warning",
            "label": "USB adapter connected",
            "detail": "The USB adapter is connected. Waiting for the vehicle OBD port to wake up.",
        }
    else:
        summary = {
            "level": "info",
            "label": "Unknown",
            "detail": "Waiting for adapter detection.",
        }

    quality["quality"] = summary
    return quality


def build_mode06_snapshot(force=False):
    """On-board monitor test results, cached.

    This issues a real OBD query, so it must not run on the dashboard's
    refresh path unthrottled; MODE06_CACHE_TTL keeps it to an occasional
    read while still refreshing during a vehicle scan.
    """
    connected = bool(app_state.connection and getattr(app_state.connection, "is_connected", bool)())
    now = time.monotonic()

    with _cache_lock:
        cached = _mode06_cache
        if (
            not force
            and cached["value"] is not None
            and cached["connected"] == connected
            and (now - cached["read_at"]) < MODE06_CACHE_TTL
        ):
            return cached["value"]

    snapshot = _build_mode06_snapshot_uncached()

    with _cache_lock:
        _mode06_cache["value"] = snapshot
        _mode06_cache["read_at"] = now
        _mode06_cache["connected"] = connected
    return snapshot


def _build_mode06_snapshot_uncached():
    # Mode 06 support differs heavily per vehicle and adapter. python-obd may not expose
    # decoded Mode 06 on every install, so this returns a safe structured snapshot.
    tests = []
    try:
        command = get_command("MONITOR_O2_B1S1") or get_command("MONITOR_O2_B1S2")
        if app_state.connection and command:
            value = safe_query(command)
            if value not in (None, "N/A"):
                tests.append({"name": "O2 monitor", "raw": str(value), "status": "available"})
    except Exception as e:
        log_error("Mode 06", e)
    return {
        "available": bool(tests),
        "tests": tests,
        "note": "Mode 06 raw monitor tests are vehicle-specific. Generic ELM adapters may expose limited data only.",
    }
