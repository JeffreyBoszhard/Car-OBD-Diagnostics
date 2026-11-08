"""The always-on background threads.

  update_loop        connection lifecycle, readiness refresh, the slower live
                     sensor set, and the periodic odometer/DPF/battery read
  rpm_update_loop    the fast RPM/speed channel, trip computer and automatic
                     VIN loading
  stream_loop        builds each dashboard frame once and publishes it to
                     every connected browser over server-sent events

The stream loop is what replaced the browser polling /api/data ten times a
second. One frame is built per tick regardless of how many tabs are open,
and nothing is built at all while no browser is listening.

Unit systems: a published frame is built with the dashboard's configured
unit system and carries a "units" marker. A browser whose own selection
differs falls back to asking /api/data directly, so a mixed-unit setup still
shows correct values without the publisher having to build every frame twice.
"""
import threading
import time

from config import (
    OBD_RECONNECT_FAST_DELAY,
    OBD_RECONNECT_IDLE_DELAY,
    OBD_RECONNECT_SLOW_DELAY,
    POLL_INTERVAL,
    RPM_POLL_INTERVAL,
    STREAM_GAUGE_INTERVAL,
    STREAM_INTERVAL,
)
from scanner_core import app_state, event_services
from scanner_core.app_state import now_time
from scanner_core.connection_services import (
    connect_obd,
    get_protocol_name,
    reconnect_backoff_active,
    safe_query,
    set_status,
)
from scanner_core.demo_services import build_demo_vehicle_profile, build_demo_vehicle_snapshot, normalize_demo_preset
from scanner_core.dtc_services import build_demo_readiness_for_current_clear_state, reset_readiness_state
from scanner_core.error_services import is_known_port_config_error, log_error
from scanner_core.extended_pid_services import refresh_extended_data
from scanner_core.live_data_services import (
    ENGINE_LOAD_COMMAND,
    RPM_COMMAND,
    SPEED_COMMAND,
    THROTTLE_COMMAND,
    build_live_item,
    get_active_live_commands,
    refresh_vehicle_stale_flags,
    set_vehicle_value,
)
from scanner_core.obd_bridge import get_command
from scanner_core.obd_services import detect_connection_hint, get_readiness_snapshot
from scanner_core.scan_payload_services import append_fuel_trim_history, current_scan_payload, save_scan_snapshot
from scanner_core.settings_services import get_demo_mode_enabled, get_demo_preset_name, get_poll_profile
from scanner_core.setup_services import get_dashboard_setup
from scanner_core.trip_services import should_autosave_on_disconnect, update_trip_state
from scanner_core.units_services import apply_unit_conversions, number_from_value
from scanner_core.vin_services import auto_refresh_vin_if_needed

# How often the odometer / DPF / battery-pack PIDs are re-read while
# connected. These change slowly and several of them are not supported at
# all, so there is no point asking often.
EXTENDED_PID_REFRESH_SECONDS = 60.0


def _autosave_trip_snapshot():
    try:
        units = str(get_dashboard_setup().get("units") or "metric")
        save_scan_snapshot("Auto-saved (adapter disconnected)", units)
    except Exception as e:
        log_error("Auto-save trip snapshot", e, affects_status=False)


def _configured_units():
    return str(get_dashboard_setup().get("units") or "metric")


def update_loop():
    last_readiness_refresh = 0
    last_extended_refresh = 0
    was_connected_real = False

    while True:
        try:
            if get_demo_mode_enabled():
                with app_state.state_lock:
                    demo_speed = float(app_state.demo_drive_state.get("speed_kmh", 0.0))
                    demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
                    demo_cleared = bool(app_state.demo_codes_cleared)
                demo_snapshot = build_demo_vehicle_snapshot(demo_speed, demo_preset)
                now = time.time()
                with app_state.state_lock:
                    previous = dict(app_state.vehicle_data)
                    app_state.vehicle_data = {
                        key: build_live_item(previous.get(key), item["label"], item["value"], now)
                        for key, item in demo_snapshot.items()
                    }
                    app_state.readiness_data = build_demo_readiness_for_current_clear_state(demo_preset, demo_cleared)
                    app_state.obd_status["connected"] = True
                    app_state.obd_status["protocol"] = "Simulator"
                    app_state.obd_status["error"] = None
                    app_state.obd_status["user_message"] = "Demo mode is generating live OBD-II values."
                    app_state.obd_status["connecting"] = False
                    app_state.obd_status["current_port"] = "Demo mode"
                    app_state.obd_status["demo_mode"] = True
                    app_state.obd_status["ecu_verified"] = True
                    app_state.obd_status["connection_hint"] = detect_connection_hint(None, demo_mode=True)
                    app_state.obd_status["last_update"] = now_time()
                    app_state.obd_status["last_successful_update"] = app_state.obd_status["last_update"]
                    app_state.obd_status["poll_interval"] = POLL_INTERVAL
                    app_state.obd_status["poll_guard_active"] = False
                    app_state.obd_status["poll_guard_reason"] = ""

                    if not app_state.vehicle_profile.get("vin"):
                        app_state.vehicle_profile.update(build_demo_vehicle_profile())

                if (time.time() - last_extended_refresh) >= EXTENDED_PID_REFRESH_SECONDS:
                    refresh_extended_data(demo_mode=True)
                    last_extended_refresh = time.time()

                time.sleep(POLL_INTERVAL)
                continue

            if not app_state.connection or not app_state.connection.is_connected():
                if was_connected_real and should_autosave_on_disconnect():
                    _autosave_trip_snapshot()
                was_connected_real = False
                reset_readiness_state()
                refresh_vehicle_stale_flags()
                set_status(
                    False,
                    error="OBD not connected. Reconnecting...",
                    user_message="Connection lost. Trying to reconnect...",
                    connecting=True
                )
                connect_obd()
                with app_state.state_lock:
                    current_error = app_state.obd_status.get("error")

                # Back off once it is clear nothing is plugged in: a
                # dashboard left open on a desk should not reopen every
                # serial port twice a second all day.
                if reconnect_backoff_active():
                    delay = OBD_RECONNECT_IDLE_DELAY
                elif is_known_port_config_error(current_error):
                    delay = OBD_RECONNECT_SLOW_DELAY
                else:
                    delay = OBD_RECONNECT_FAST_DELAY
                time.sleep(delay)
                continue

            with app_state.state_lock:
                previous_data = dict(app_state.vehicle_data)

            data = {}
            cycle_time = time.time()

            for key, item in get_active_live_commands().items():
                label, command = item
                value = safe_query(command)
                data[key] = {
                    **build_live_item(previous_data.get(key), label, value, cycle_time),
                }

            protocol = get_protocol_name()
            now = time.time()

            if now - last_readiness_refresh >= 5:
                readiness_snapshot = get_readiness_snapshot(app_state.connection, app_state.obd_lock, get_command)
                with app_state.state_lock:
                    app_state.readiness_data = readiness_snapshot
                last_readiness_refresh = now

            if now - last_extended_refresh >= EXTENDED_PID_REFRESH_SECONDS:
                refresh_extended_data(demo_mode=False)
                last_extended_refresh = now

            with app_state.state_lock:
                for fast_key, fallback_label in {
                    "rpm": "RPM",
                    "speed": "Speed",
                    "engine_load": "Engine load",
                    "throttle": "Throttle position",
                }.items():
                    data[fast_key] = dict(app_state.vehicle_data.get(fast_key, previous_data.get(fast_key, {
                        "label": fallback_label,
                        "value": "N/A"
                    })))
                app_state.vehicle_data = data
                app_state.obd_status["connected"] = True
                app_state.obd_status["protocol"] = protocol
                app_state.obd_status["error"] = None
                app_state.obd_status["user_message"] = "Live data is updating."
                app_state.obd_status["connecting"] = False
                app_state.obd_status["demo_mode"] = False
                app_state.obd_status["limited_mode"] = bool(get_poll_profile()["limited"])
                app_state.obd_status["last_update"] = time.strftime("%H:%M:%S")
                app_state.obd_status["last_successful_update"] = app_state.obd_status["last_update"]
                app_state.obd_status["connection_hint"] = detect_connection_hint(app_state.connection, None)

            was_connected_real = True
            time.sleep(app_state.current_live_poll_interval)

        except Exception as e:
            log_error("Update loop", e)
            set_status(False, error=str(e))
            time.sleep(max(app_state.current_live_poll_interval, 0.3))


def rpm_update_loop():
    last_aux_refresh = 0.0
    aux_interval = max(0.12, RPM_POLL_INTERVAL * 3)

    while True:
        try:
            if get_demo_mode_enabled():
                with app_state.state_lock:
                    demo_speed = float(app_state.demo_drive_state.get("speed_kmh", 0.0))
                    demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))
                demo_snapshot = build_demo_vehicle_snapshot(demo_speed, demo_preset)
                set_vehicle_value("rpm", "RPM", demo_snapshot.get("rpm", {}).get("value", "N/A"))
                set_vehicle_value("speed", "Speed", demo_snapshot.get("speed", {}).get("value", "N/A"))
                update_trip_state(
                    number_from_value(demo_snapshot.get("speed", {}).get("value")),
                    number_from_value(demo_snapshot.get("rpm", {}).get("value")),
                    number_from_value(demo_snapshot.get("coolant_temp", {}).get("value")),
                    number_from_value(demo_snapshot.get("maf", {}).get("value")),
                )
                time.sleep(RPM_POLL_INTERVAL)
                continue

            if not app_state.connection or not app_state.connection.is_connected():
                app_state.vin_autoload_attempted = False
                time.sleep(0.08)
                continue

            # Keep the critical gauges first. Every extra OBD query is serial and can
            # make RPM/speed feel delayed on slower ELM327 adapters.
            rpm_value = safe_query(RPM_COMMAND)
            set_vehicle_value("rpm", "RPM", rpm_value)
            speed_value = safe_query(SPEED_COMMAND)
            set_vehicle_value("speed", "Speed", speed_value)

            with app_state.state_lock:
                coolant_value = app_state.vehicle_data.get("coolant_temp", {}).get("value")
                maf_value = app_state.vehicle_data.get("maf", {}).get("value")
            update_trip_state(
                number_from_value(speed_value),
                number_from_value(rpm_value),
                number_from_value(coolant_value),
                number_from_value(maf_value),
            )

            now = time.time()
            if now - last_aux_refresh >= aux_interval:
                engine_load_value = safe_query(ENGINE_LOAD_COMMAND)
                set_vehicle_value("engine_load", "Engine load", engine_load_value)
                throttle_value = safe_query(THROTTLE_COMMAND)
                set_vehicle_value("throttle", "Throttle position", throttle_value)
                last_aux_refresh = now

            with app_state.state_lock:
                vin = app_state.vehicle_profile.get("vin", "")
                vin_status = app_state.vehicle_profile.get("vin_status", "idle")
                should_refresh_vin = (
                    not vin
                    and not app_state.vin_autoload_attempted
                    and vin_status not in {"loading", "ready"}
                )

            if should_refresh_vin:
                app_state.vin_autoload_attempted = True
                threading.Thread(target=auto_refresh_vin_if_needed, daemon=True).start()

            time.sleep(RPM_POLL_INTERVAL)
        except Exception as e:
            log_error("RPM update loop", e, affects_status=False)
            time.sleep(0.08)


def _gauge_frame(units):
    with app_state.state_lock:
        status = dict(app_state.obd_status)
        vehicle = {
            "rpm": dict(app_state.vehicle_data.get("rpm", {"label": "RPM", "value": "N/A"})),
            "speed": dict(app_state.vehicle_data.get("speed", {"label": "Speed", "value": "N/A"})),
        }

    return {
        "connected": bool(status.get("connected")),
        "demo_mode": bool(status.get("demo_mode")),
        "units": units,
        "vehicle": apply_unit_conversions(vehicle, units),
    }


def stream_loop():
    """Build and publish dashboard frames while at least one browser listens."""
    last_live_push = 0.0
    last_gauge_push = 0.0

    while True:
        try:
            if not event_services.has_subscribers():
                # Nothing to feed: forget the stale frames so a browser that
                # connects later is not shown a snapshot from minutes ago.
                event_services.clear_last_frames()
                time.sleep(0.4)
                continue

            now = time.monotonic()
            units = _configured_units()

            if (now - last_gauge_push) >= STREAM_GAUGE_INTERVAL:
                event_services.publish("gauges", _gauge_frame(units))
                last_gauge_push = now

            if (now - last_live_push) >= STREAM_INTERVAL:
                payload = current_scan_payload(units)
                payload["units"] = units
                with app_state.state_lock:
                    payload["dtc_status"] = dict(app_state.dtc_status)
                event_services.publish("live", payload)
                # Sampling the fuel trim is rate-limited inside this call.
                append_fuel_trim_history(payload)
                last_live_push = now

            time.sleep(STREAM_GAUGE_INTERVAL / 2)
        except Exception as e:
            log_error("Live data stream", e, affects_status=False)
            time.sleep(0.5)


def start_background_threads():
    """Start every background thread exactly once."""
    for target in (update_loop, rpm_update_loop, stream_loop):
        threading.Thread(target=target, name=target.__name__, daemon=True).start()
