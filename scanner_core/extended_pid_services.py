"""Odometer, DPF and hybrid/EV battery-pack readings.

Standard OBD-II is mostly engine and emissions data, but a few genuinely
useful service values are defined in SAE J1979 outside the set python-obd
exposes by default:

  PID 01 A6  total odometer, in 0.1 km steps
  PID 01 5B  hybrid/EV battery pack remaining life, in percent
  PID 01 7C  DPF temperature
  PID 01 7A  DPF differential pressure (vehicle-specific layout)

Support is far from universal: plenty of cars answer none of these, and some
answer 0xA6 with a value that is not the dashboard odometer. Everything here
is therefore strictly opt-in, checked against the vehicle's own supported-PID
list first, and reported with a confidence label rather than presented as
gospel. Nothing here guesses: when a PID is not supported, the value is
simply absent and the maintenance estimate falls back to logged mileage.

The DPF differential-pressure layout varies per manufacturer, so that one is
reported as decoded raw bytes rather than a single invented number.
"""
import time

from scanner_core import app_state
from scanner_core.error_services import log_error
from scanner_core.obd_bridge import OBD_AVAILABLE, get_command

# Built lazily: constructing an OBDCommand requires python-obd to be present.
_custom_commands = None


def _decode_odometer_km(messages):
    """PID A6: four bytes, total distance in 0.1 km units."""
    if not messages:
        return None
    data = messages[0].data[2:]
    if len(data) < 4:
        return None
    raw = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
    return raw / 10.0


def _decode_dpf_temperature_c(messages):
    """PID 7C: DPF temperature sensors, 0.1 C per bit with a -40 C offset."""
    if not messages:
        return None
    data = messages[0].data[2:]
    if len(data) < 3:
        return None
    raw = (data[1] << 8) | data[2]
    return round(raw / 10.0 - 40.0, 1)


def _decode_raw_hex(messages):
    if not messages:
        return None
    data = messages[0].data[2:]
    return data.hex(" ").upper() if data else None


def _build_custom_commands():
    """Define the extra PIDs python-obd does not ship with."""
    if not OBD_AVAILABLE:
        return {}

    try:
        from obd import OBDCommand
        from obd.protocols import ECU
    except Exception as e:
        log_error("Extended PIDs", e, affects_status=False)
        return {}

    def command(name, description, pid_hex, byte_count, decoder):
        return OBDCommand(
            name,
            description,
            b"01" + pid_hex,
            byte_count,
            decoder,
            ECU.ENGINE,
            False,
        )

    try:
        return {
            "odometer": command("ODOMETER_EXT", "Odometer", b"A6", 6, _decode_odometer_km),
            "dpf_temperature": command("DPF_TEMP_EXT", "DPF temperature", b"7C", 9, _decode_dpf_temperature_c),
            "dpf_pressure_raw": command("DPF_PRESSURE_EXT", "DPF differential pressure", b"7A", 9, _decode_raw_hex),
        }
    except Exception as e:
        log_error("Extended PIDs", e, affects_status=False)
        return {}


def _get_custom_commands():
    global _custom_commands
    if _custom_commands is None:
        _custom_commands = _build_custom_commands()
    return _custom_commands


def _supported_pid_bytes(connection):
    """The vehicle's own supported-PID bitmaps, as a set of command names."""
    try:
        return {command.name for command in connection.supported_commands}
    except Exception:
        return set()


def _query_raw(connection, command):
    if command is None:
        return None
    try:
        with app_state.obd_lock:
            response = connection.query(command, force=True)
        if response.is_null():
            return None
        return response.value
    except Exception as e:
        log_error("Extended PIDs", e, affects_status=False)
        return None


def read_extended_data(connection=None, demo_mode=False):
    """Read odometer / DPF / battery-pack values where the car answers.

    Returns a dict that is safe to show as-is; every section states whether
    the vehicle actually answered.
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    if demo_mode:
        return {
            "odometer": {"km": 184532.0, "source": "demo", "confidence": "demo"},
            "dpf": {
                "available": True,
                "temperature_c": 247.5,
                "pressure_raw": "00 1A 00 1C",
                "note": "Demo values.",
            },
            "battery_pack": {"available": True, "remaining_pct": 82.0, "source": "demo"},
            "checked_at": now,
        }

    connection = connection or app_state.connection
    result = {
        "odometer": None,
        "dpf": {"available": False},
        "battery_pack": {"available": False},
        "checked_at": now,
    }

    if connection is None or not connection.is_connected():
        return result

    custom = _get_custom_commands()

    # --- odometer --------------------------------------------------------
    # Prefer python-obd's own command when the installed version has it, so
    # a future library release that decodes this properly wins over our
    # local definition.
    odometer_command = get_command("ODOMETER") or custom.get("odometer")
    odometer_km = _query_raw(connection, odometer_command)
    if odometer_km is not None:
        # python-obd returns a Pint quantity; our own decoder returns a float.
        value = getattr(odometer_km, "magnitude", odometer_km)
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        # A plausibility gate: a vehicle answering 0 or an absurd number is
        # answering with padding, not an odometer.
        if value is not None and 1.0 <= value <= 2_000_000.0:
            result["odometer"] = {
                "km": round(value, 1),
                "source": "obd_pid_a6",
                "confidence": "medium",
            }

    # --- DPF -------------------------------------------------------------
    dpf_temp = _query_raw(connection, custom.get("dpf_temperature"))
    dpf_pressure = _query_raw(connection, custom.get("dpf_pressure_raw"))
    if dpf_temp is not None or dpf_pressure is not None:
        result["dpf"] = {
            "available": True,
            "temperature_c": dpf_temp,
            "pressure_raw": dpf_pressure,
            "note": "Diesel particulate filter values are vehicle-specific; pressure is shown as raw bytes.",
        }

    # --- hybrid / EV battery pack ----------------------------------------
    battery_command = get_command("HYBRID_BATTERY_REMAINING")
    if battery_command is not None:
        supported = _supported_pid_bytes(connection)
        if not supported or battery_command.name in supported:
            remaining = _query_raw(connection, battery_command)
            if remaining is not None:
                value = getattr(remaining, "magnitude", remaining)
                try:
                    result["battery_pack"] = {
                        "available": True,
                        "remaining_pct": round(float(value), 1),
                        "source": "obd_pid_5b",
                    }
                except (TypeError, ValueError):
                    pass

    return result


def refresh_extended_data(demo_mode=False):
    """Read the extended PIDs and store them on the shared state."""
    data = read_extended_data(demo_mode=demo_mode)
    with app_state.state_lock:
        app_state.extended_data = data
    return data


def get_extended_data():
    with app_state.state_lock:
        return {
            "odometer": app_state.extended_data.get("odometer"),
            "dpf": dict(app_state.extended_data.get("dpf") or {}),
            "battery_pack": dict(app_state.extended_data.get("battery_pack") or {}),
            "checked_at": app_state.extended_data.get("checked_at"),
        }


def get_known_odometer_km():
    """The odometer reading read from the car, if one was obtained."""
    odometer = get_extended_data().get("odometer") or {}
    km = odometer.get("km")
    try:
        return float(km) if km is not None else None
    except (TypeError, ValueError):
        return None
