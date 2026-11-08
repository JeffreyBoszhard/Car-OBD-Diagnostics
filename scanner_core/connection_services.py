"""OBD adapter connection lifecycle: opening and closing the serial link,
querying commands defensively, and normalising raw python-obd values into
short display strings.

Connecting is done in a deliberate order rather than by luck:

  1. the port the user picked, if they picked one
  2. the port the last verified ECU session used
  3. remaining ports, most adapter-looking first (see obd_services.score_port)

and a candidate is only accepted once the ECU behind it answers a real PID.
A port that merely opens is reported as "adapter found, ECU silent" instead
of a successful connection, which is the honest answer and is what tells the
user to turn the ignition on.
"""
import time

from config import (
    OBD_CONNECT_ATTEMPTS,
    OBD_CONNECT_RETRY_DELAY,
    OBD_CONNECT_TIMEOUT,
    OBD_RECONNECT_BACKOFF_AFTER,
    OBD_VERIFY_ECU_ON_CONNECT,
    POLL_INTERVAL,
)
from scanner_core import app_state
from scanner_core.error_services import friendly_message, log_error
from scanner_core.live_data_services import apply_poll_guard_error, apply_poll_guard_success
from scanner_core.obd_bridge import OBD_AVAILABLE, OBD_IMPORT_ERROR, get_command, obd
from scanner_core.obd_services import (
    adapter_name,
    detect_connection_hint,
    invalidate_port_cache,
    list_serial_ports,
    normalize_port_name,
    read_adapter_voltage,
    verify_ecu_responds,
)
from scanner_core.settings_services import (
    apply_demo_preset_state,
    get_configured_port,
    get_demo_mode_enabled,
    get_demo_preset_name,
    get_last_good_port,
    get_poll_profile,
    set_last_good_port,
)


def close_obd_connection():
    old_connection = app_state.connection
    app_state.connection = None
    if old_connection:
        try:
            old_connection.close()
        except Exception as e:
            log_error("Close OBD connection", e)


def build_connection_port_candidates(configured_port):
    """Ports to try, in the order they should be tried.

    When the user picked a port explicitly, that port is tried first and the
    others are still kept as a fallback - an adapter that moved to another
    COM number after a reboot should not need a manual fix.
    """
    detected_ports = list_serial_ports()
    candidates = []
    seen = set()

    def add_candidate(port):
        cleaned = str(port or "").strip()
        if not cleaned:
            return
        key = normalize_port_name(cleaned)
        if key in seen:
            return
        seen.add(key)
        candidates.append(cleaned)

    add_candidate(configured_port)
    add_candidate(get_last_good_port())

    # list_serial_ports() is already sorted most-adapter-looking first.
    for item in detected_ports:
        add_candidate(item.get("device"))

    if not candidates:
        # No ports at all: let python-obd run its own scan as a last resort.
        candidates.append(None)

    return candidates


def open_obd_candidate(port):
    kwargs = {
        "fast": False,
        "timeout": OBD_CONNECT_TIMEOUT,
        "check_voltage": False,
    }
    return obd.OBD(port, **kwargs) if port else obd.OBD(**kwargs)


def _describe_candidate(port):
    return port or "auto-detect"


def _accept_connection(new_connection, port):
    """Record a verified connection on the shared state."""
    app_state.connection = new_connection
    protocol = new_connection.protocol_name() or "Unknown"
    name = adapter_name(new_connection)
    voltage = read_adapter_voltage(new_connection, app_state.obd_lock)

    set_status(
        True,
        protocol=protocol,
        user_message=f"Connected via {protocol} on {_describe_candidate(port)}.",
        connecting=False,
    )
    with app_state.state_lock:
        app_state.obd_status["current_port"] = port
        app_state.obd_status["adapter_name"] = name
        app_state.obd_status["adapter_voltage"] = voltage
        app_state.obd_status["ecu_verified"] = True
        app_state.obd_status["connection_hint"] = detect_connection_hint(app_state.connection, None)

    app_state.connect_failure_streak = 0
    if port:
        set_last_good_port(port)
    print(f"Connected: {protocol} on {_describe_candidate(port)}")
    return True


def connect_obd():
    with app_state.connect_lock:
        port = None
        last_error = None
        adapter_seen = False
        try:
            app_state.query_error_streak = 0
            app_state.current_live_poll_interval = POLL_INTERVAL
            demo_mode = get_demo_mode_enabled()
            with app_state.state_lock:
                app_state.obd_status["demo_mode"] = demo_mode
                app_state.obd_status["limited_mode"] = bool(get_poll_profile()["limited"])
                app_state.obd_status["poll_interval"] = POLL_INTERVAL
                app_state.obd_status["poll_guard_active"] = False
                app_state.obd_status["poll_guard_reason"] = ""
                app_state.obd_status["ecu_verified"] = False
                app_state.obd_status["connect_attempts"] = app_state.connect_failure_streak

            close_obd_connection()

            if demo_mode:
                preset_name, default_speed = apply_demo_preset_state(get_demo_preset_name(), reset_speed=False)
                set_status(
                    True,
                    protocol="Simulator",
                    user_message="Demo mode is active. Simulating a standard OBD-II session.",
                    connecting=False,
                )
                with app_state.state_lock:
                    app_state.obd_status["current_port"] = "Demo mode"
                    app_state.obd_status["adapter_name"] = "Simulated ELM327 v1.5"
                    app_state.obd_status["adapter_voltage"] = 14.1
                    app_state.obd_status["ecu_verified"] = True
                    app_state.obd_status["connection_hint"] = detect_connection_hint(None, demo_mode=True)
                    app_state.demo_drive_state["preset"] = preset_name
                    if not app_state.demo_drive_state.get("speed_kmh"):
                        app_state.demo_drive_state["speed_kmh"] = default_speed
                app_state.connect_failure_streak = 0
                return

            if not OBD_AVAILABLE:
                set_status(
                    False,
                    error=f"python-obd did not load: {OBD_IMPORT_ERROR}",
                    user_message=friendly_message(
                        f"python-obd did not load: {OBD_IMPORT_ERROR}",
                        source="Connect OBD",
                    ),
                    connecting=False,
                )
                return

            configured_port = get_configured_port()
            # A fresh enumeration per connect round: an adapter plugged in
            # ten seconds ago must be found without restarting the app.
            invalidate_port_cache()
            candidates = build_connection_port_candidates(configured_port)

            with app_state.state_lock:
                app_state.obd_status["current_port"] = configured_port or (candidates[0] if candidates else None)
                app_state.obd_status["connecting"] = True
                app_state.obd_status["user_message"] = (
                    f"Connecting to {configured_port}..." if configured_port else "Searching for an OBD adapter..."
                )
                app_state.obd_status["last_update"] = time.strftime("%H:%M:%S")

            for attempt in range(1, max(1, int(OBD_CONNECT_ATTEMPTS or 1)) + 1):
                for port in candidates:
                    try:
                        with app_state.state_lock:
                            app_state.obd_status["current_port"] = port
                            app_state.obd_status["user_message"] = f"Connecting to {_describe_candidate(port)}..."
                        new_connection = open_obd_candidate(port)

                        if new_connection and new_connection.is_connected():
                            adapter_seen = True
                            if not OBD_VERIFY_ECU_ON_CONNECT:
                                _accept_connection(new_connection, port)
                                return

                            ecu_ok, ecu_detail = verify_ecu_responds(
                                new_connection, app_state.obd_lock, get_command
                            )
                            if ecu_ok:
                                _accept_connection(new_connection, port)
                                return

                            # The serial link is up but the car is silent.
                            # Keep looking on other ports; if none answers,
                            # the error below says the ignition is likely off
                            # instead of claiming no adapter was found.
                            last_error = RuntimeError(f"ECU did not answer on {_describe_candidate(port)}: {ecu_detail}")
                            try:
                                new_connection.close()
                            except Exception:
                                pass
                            continue

                        if new_connection and str(new_connection.status() or "").startswith("ELM"):
                            adapter_seen = True

                        try:
                            if new_connection:
                                new_connection.close()
                        except Exception:
                            pass
                        last_error = RuntimeError("No OBD connection found.")
                    except Exception as exc:
                        last_error = exc
                        lowered = str(exc).lower()
                        if "permission" in lowered or "toegang geweigerd" in lowered or "access is denied" in lowered:
                            # The port exists but something else owns it;
                            # trying the rest of the list will not help.
                            break

                if attempt < max(1, int(OBD_CONNECT_ATTEMPTS or 1)):
                    time.sleep(OBD_CONNECT_RETRY_DELAY)

            app_state.connect_failure_streak += 1
            error_message = str(last_error or "No OBD connection found.")
            set_status(
                False,
                error=error_message,
                user_message=friendly_message(error_message, source="Connect OBD", port=port),
                connecting=False,
            )
            with app_state.state_lock:
                app_state.obd_status["ecu_verified"] = False
                app_state.obd_status["connect_attempts"] = app_state.connect_failure_streak
                app_state.obd_status["connection_hint"] = detect_connection_hint(None, error_message)
                if adapter_seen:
                    app_state.obd_status["connection_hint"] = {
                        "kind": "ignition_likely_off",
                        "label": "Adapter found, ECU silent",
                        "detail": "The adapter answers but the ECU does not. Turn the ignition on and try again.",
                    }

            if app_state.connect_failure_streak <= 1 or app_state.connect_failure_streak % 10 == 0:
                print(f"No OBD connection ({app_state.connect_failure_streak} attempts).")

        except Exception as e:
            close_obd_connection()
            app_state.connect_failure_streak += 1
            set_status(
                False,
                error=str(e),
                user_message=friendly_message(e, source="Connect OBD", port=port),
                connecting=False,
            )
            with app_state.state_lock:
                app_state.obd_status["connection_hint"] = detect_connection_hint(None, e)
            log_error("Connect OBD", e)


def reconnect_backoff_active():
    """True once enough rounds failed that the loop should slow down."""
    return app_state.connect_failure_streak >= OBD_RECONNECT_BACKOFF_AFTER


def set_status(connected, protocol=None, error=None, user_message=None, connecting=None):
    with app_state.state_lock:
        app_state.obd_status["connected"] = connected
        app_state.obd_status["protocol"] = protocol or "Unknown"
        app_state.obd_status["error"] = error
        app_state.obd_status["limited_mode"] = bool(get_poll_profile()["limited"])
        if user_message is not None:
            app_state.obd_status["user_message"] = user_message
        if connecting is not None:
            app_state.obd_status["connecting"] = connecting
        app_state.obd_status["last_update"] = time.strftime("%H:%M:%S")
        app_state.obd_status["connection_hint"] = detect_connection_hint(
            app_state.connection, error, app_state.obd_status.get("demo_mode", False)
        )
        if connected:
            app_state.obd_status["last_successful_update"] = app_state.obd_status["last_update"]
        else:
            app_state.obd_status["ecu_verified"] = False


def simplify_fuel_status_text(text):
    lowered = str(text or "").lower()
    if "closed loop" in lowered:
        return "Closed loop"
    if "open loop" in lowered:
        if "engine load" in lowered:
            return "Open loop - engine load"
        if "deceleration" in lowered or "fuel cut" in lowered:
            return "Open loop - fuel cut"
        if "insufficient" in lowered or "temperature" in lowered:
            return "Open loop - warming up"
        if "system failure" in lowered or "fault" in lowered:
            return "Open loop - fault"
        return "Open loop"
    return str(text or "").strip()


def simplify_obd_value(value):
    if value is None:
        return "N/A"

    if isinstance(value, (list, tuple)):
        cleaned = [simplify_obd_value(item) for item in value if item is not None]
        cleaned = [item for item in cleaned if item and item != "N/A"]
        if not cleaned:
            return "N/A"
        unique = []
        for item in cleaned:
            if item not in unique:
                unique.append(item)
        return unique[0] if len(unique) == 1 else " / ".join(unique)

    text = str(value).strip()
    if not text:
        return "N/A"

    return simplify_fuel_status_text(text)


def safe_query(command):
    if command is None:
        return "N/A"

    if not app_state.connection or not app_state.connection.is_connected():
        return "N/A"

    try:
        if hasattr(app_state.connection, "supports") and not app_state.connection.supports(command):
            return "N/A"

        with app_state.obd_lock:
            response = app_state.connection.query(command)

        if response.is_null():
            return "N/A"

        apply_poll_guard_success()
        return simplify_obd_value(response.value)

    except Exception as e:
        apply_poll_guard_error()
        log_error("Live data query", e, affects_status=False)
        return "N/A"


def get_protocol_name():
    try:
        if app_state.connection and app_state.connection.is_connected():
            return app_state.connection.protocol_name()
    except Exception as e:
        log_error("Read protocol", e, affects_status=False)

    return "Unknown"
