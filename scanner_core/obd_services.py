"""Adapter and ECU detection: which serial ports look like an OBD adapter,
whether the adapter answers, and whether the car behind it answers.

The old flow reported "connected" as soon as python-obd's own is_connected()
was true. On ELM327 clones that is optimistic: the serial link comes up and
the adapter chats happily while the ECU says nothing, which showed up as a
connected scanner with nothing but N/A values. Detection here is therefore
split into the three things that can independently fail:

  1. is there a serial port that looks like an adapter        (port scoring)
  2. does the adapter itself answer                           (ELM identify)
  3. does the ECU behind it answer a real PID                 (ECU verify)

Each step is reported separately, so the dashboard can say "adapter found,
ignition off" instead of just "not connected".
"""
import re
import threading
import time
from contextlib import nullcontext

from config import SERIAL_PORT_CACHE_TTL

try:
    import obd
    from obd.OBDResponse import StatusTest
    from obd.utils import OBDStatus
except Exception:
    obd = None
    OBDStatus = None
    StatusTest = None

try:
    from serial.tools import list_ports
except Exception:
    list_ports = None


READINESS_ORDER = [
    "misfire",
    "fuel_system",
    "components",
    "catalyst",
    "heated_catalyst",
    "evap_system",
    "secondary_air_system",
    "ac_refrigerant",
    "oxygen_sensor",
    "oxygen_sensor_heater",
    "egr_system",
]


FREEZE_FRAME_COMMANDS = {
    "trigger_code": "FREEZE_DTC",
    "rpm": "DTC_RPM",
    "speed": "DTC_SPEED",
    "coolant_temp": "DTC_COOLANT_TEMP",
    "engine_load": "DTC_ENGINE_LOAD",
    "fuel_status": "DTC_FUEL_STATUS",
    "short_fuel_trim_1": "DTC_SHORT_FUEL_TRIM_1",
    "long_fuel_trim_1": "DTC_LONG_FUEL_TRIM_1",
    "intake_pressure": "DTC_INTAKE_PRESSURE",
    "intake_temp": "DTC_INTAKE_TEMP",
    "maf": "DTC_MAF",
    "throttle": "DTC_THROTTLE_POS",
    "control_voltage": "DTC_CONTROL_MODULE_VOLTAGE",
}

# The PIDs used to prove an ECU is really answering, cheapest first. PIDS_A
# (0x00) is mandatory on every OBD-II compliant vehicle and is answered even
# with the engine off but ignition on, which makes it a better probe than
# RPM.
ECU_PROBE_COMMANDS = ("PIDS_A", "RPM", "COOLANT_TEMP", "SPEED")

# USB-serial chips and adapter names that show up in real ELM327 hardware.
# A port whose description matches scores higher and is tried first, which
# matters on a laptop with a dozen virtual COM ports.
ADAPTER_HINT_PATTERNS = (
    (re.compile(r"obd|elm\s*327|elm327|vgate|obdlink|stn\d{4}|scantool", re.I), 50),
    (re.compile(r"ch340|ch341|cp210|ft232|ftdi|pl2303|prolific|silicon\s*labs", re.I), 30),
    (re.compile(r"usb.*serial|serial.*usb", re.I), 15),
    (re.compile(r"bluetooth|rfcomm|spp", re.I), 12),
)

# Virtual ports that are never an OBD adapter.
ADAPTER_EXCLUDE_PATTERNS = (
    re.compile(r"bluetooth.*(link|network)|debug|printer|modem|mouse", re.I),
)

_port_cache_lock = threading.Lock()
_port_cache = {"ports": [], "read_at": 0.0}


def _safe_query(connection, lock, command):
    if command is None or connection is None or not connection.is_connected():
        return None

    try:
        if hasattr(connection, "supports") and not connection.supports(command):
            return None
    except Exception:
        return None

    try:
        with lock:
            response = connection.query(command)
        if response.is_null():
            return None
        return response.value
    except Exception:
        return None


def normalize_port_name(value):
    """Compare port names case-insensitively on Windows only.

    Windows COM ports are conventionally uppercase and case-insensitive;
    Linux and macOS device paths (/dev/ttyUSB0, /dev/rfcomm0) are
    case-sensitive, and upper-casing them turns a working port into a
    non-existent one.
    """
    text = str(value or "").strip()
    if re.match(r"^com\d+$", text, re.IGNORECASE):
        return text.upper()
    return text


def ports_match(left, right):
    return bool(left) and bool(right) and normalize_port_name(left) == normalize_port_name(right)


def score_port(port):
    """How likely this serial port is an OBD adapter. Higher is better."""
    haystack = " ".join(
        str(part or "")
        for part in (port.get("description"), port.get("hwid"), port.get("device"), port.get("manufacturer"))
    )

    for pattern in ADAPTER_EXCLUDE_PATTERNS:
        if pattern.search(haystack):
            return -10

    score = 0
    for pattern, weight in ADAPTER_HINT_PATTERNS:
        if pattern.search(haystack):
            score += weight

    device = str(port.get("device") or "")
    if re.search(r"/dev/(ttyUSB|ttyACM|rfcomm|cu\.usb|tty\.usb)", device, re.I):
        score += 10
    if re.match(r"^COM\d+$", device, re.I):
        score += 5

    return score


def list_serial_ports(force=False):
    """Detected serial ports, newest-first by adapter likelihood.

    Cached for SERIAL_PORT_CACHE_TTL seconds: enumerating ports hits the
    registry on Windows and sysfs on Linux, and the live-data stream used to
    ask for this list several times per second.
    """
    if list_ports is None:
        return []

    now = time.monotonic()
    with _port_cache_lock:
        if not force and _port_cache["read_at"] and (now - _port_cache["read_at"]) < SERIAL_PORT_CACHE_TTL:
            return [dict(item) for item in _port_cache["ports"]]

    ports = []
    try:
        for port in list_ports.comports():
            hwid = port.hwid or ""
            # On Linux, pyserial always enumerates the legacy /dev/ttyS0-31
            # motherboard UART stubs even when no real hardware is behind
            # them; it flags those with hwid "n/a". Real USB/Bluetooth
            # adapters always report a proper hwid, so this filters out the
            # phantom entries without hiding a genuine serial adapter.
            if hwid.strip().lower() == "n/a":
                continue
            entry = {
                "device": port.device,
                "description": port.description or port.device,
                "hwid": hwid,
                "manufacturer": getattr(port, "manufacturer", "") or "",
            }
            entry["score"] = score_port(entry)
            entry["likely_adapter"] = entry["score"] >= 15
            ports.append(entry)
    except Exception:
        return []

    ports.sort(key=lambda item: (-item["score"], item["device"]))

    with _port_cache_lock:
        _port_cache["ports"] = [dict(item) for item in ports]
        _port_cache["read_at"] = now

    return ports


def invalidate_port_cache():
    with _port_cache_lock:
        _port_cache["read_at"] = 0.0


def adapter_name(connection):
    """The ELM327 identification string, when the adapter gave one."""
    for attribute in ("elm_version", "elm_voltage"):
        try:
            value = getattr(connection, attribute, None)
            if callable(value):
                value = value()
            if value:
                return str(value)
        except Exception:
            continue
    return ""


def read_adapter_voltage(connection, lock=None):
    """The adapter's own measured battery voltage (ELM327 "AT RV").

    This comes from the adapter, not the ECU, so it answers even with the
    ignition off - which is exactly what distinguishes "adapter is plugged
    into a dead port" from "ignition is off".
    """
    if connection is None:
        return None
    try:
        command = getattr(obd.commands, "ELM_VOLTAGE", None) if obd else None
        if command is None:
            return None
        with (lock or nullcontext()):
            response = connection.query(command)
        if response.is_null():
            return None
        value = getattr(response.value, "magnitude", response.value)
        return round(float(value), 1)
    except Exception:
        return None


def verify_ecu_responds(connection, lock, get_command):
    """Ask the ECU for a real PID. Returns (ok, detail).

    This is what separates "the adapter is talking to us" from "the car is
    talking to us". force=True is used so a vehicle that reports an empty
    supported-PID list is still probed instead of being skipped.
    """
    if connection is None or not connection.is_connected():
        return False, "No serial link to the adapter."

    for name in ECU_PROBE_COMMANDS:
        command = get_command(name)
        if command is None:
            continue
        try:
            with lock:
                response = connection.query(command, force=True)
        except Exception as e:
            return False, f"ECU probe failed: {e}"

        if response is not None and not response.is_null():
            return True, f"ECU answered {name}."

    return False, "ECU did not answer any standard PID."


def connection_quality_snapshot(connection, connecting=False, error=None):
    phase = "Not Connected"

    try:
        if connection and hasattr(connection, "status"):
            phase = str(connection.status() or "Not Connected")
    except Exception:
        phase = "Not Connected"

    car_connected = bool(connection and connection.is_connected())
    adapter_connected = phase in {"ELM Connected", "OBD Connected", "Car Connected"} or car_connected
    port_powered = phase in {"OBD Connected", "Car Connected"} or car_connected
    live_data_active = bool(car_connected and not connecting)

    return {
        "phase": phase,
        "adapter_connected": adapter_connected,
        "port_powered": port_powered,
        "car_connected": car_connected,
        "live_data_active": live_data_active,
    }


def detect_connection_hint(connection, error=None, demo_mode=False):
    if demo_mode:
        return {
            "kind": "demo",
            "label": "Demo mode active",
            "detail": "The app is simulating a standard OBD-II session for UI testing.",
        }

    message = str(error or "").lower()
    quality = connection_quality_snapshot(connection, False, error)
    phase = quality["phase"].lower()

    if "obd adapter" in message or "could not open port" in message or "not detected" in message:
        return {
            "kind": "adapter_missing",
            "label": "Adapter not detected",
            "detail": "Plug in the OBD adapter or choose the correct adapter port.",
        }

    if "busy or blocked" in message or "access is denied" in message:
        return {
            "kind": "port_busy",
            "label": "Adapter port is busy",
            "detail": "Another program is holding the adapter port. Close other OBD software.",
        }

    if "ecu did not answer" in message:
        return {
            "kind": "ignition_likely_off",
            "label": "Ignition likely off",
            "detail": "The adapter answers, but the ECU does not. Turn the ignition on.",
        }

    if quality["adapter_connected"] and not quality["car_connected"]:
        if "elm connected" in phase:
            return {
                "kind": "ignition_likely_off",
                "label": "Ignition likely off",
                "detail": "The adapter responds, but the ECU is not answering yet.",
            }
        return {
            "kind": "ecu_no_response",
            "label": "ECU not responding",
            "detail": "The adapter is present, but the ECU is not replying on standard OBD.",
        }

    if quality["car_connected"]:
        return {
            "kind": "live",
            "label": "ECU connected",
            "detail": "Standard OBD live data is available.",
        }

    return {
        "kind": "searching",
        "label": "Searching for adapter",
        "detail": "Waiting for an OBD adapter or a live ECU response.",
    }


def run_connection_test(obd_module, configured_port, timeout=1.0, attempts=2, retry_delay=0.5,
                        get_command=None):
    """The Service page's step-by-step connection test.

    Reports each stage separately - port present, adapter answering, protocol
    negotiated, ECU answering a real PID - because any one of them can be the
    thing that is broken.
    """
    detected_ports = list_serial_ports(force=True)
    selected_port_present = bool(
        configured_port
        and any(ports_match(item.get("device"), configured_port) for item in detected_ports)
    )
    likely_adapters = [item for item in detected_ports if item.get("likely_adapter")]
    any_usb_serial_present = selected_port_present or bool(detected_ports)

    if selected_port_present:
        adapter_detail = str(configured_port)
    elif likely_adapters:
        adapter_detail = ", ".join(item.get("device", "") for item in likely_adapters[:4])
    elif detected_ports:
        adapter_detail = ", ".join(item.get("device", "") for item in detected_ports[:4])
    else:
        adapter_detail = "No serial ports detected"

    if obd_module is None:
        return {
            "success": False,
            "phase": "Library unavailable",
            "steps": [
                {"name": "Adapter port detected", "ok": any_usb_serial_present, "detail": adapter_detail},
                {"name": "Python OBD library", "ok": False, "detail": "python-obd is not available."},
            ],
        }

    test_connection = None
    last_exception = None
    phase = "Adapter port detected" if any_usb_serial_present else "No serial ports detected"
    protocol = "Unknown"
    ecu_ok = False
    ecu_detail = "No ECU response yet"
    probe_lock = threading.Lock()

    try:
        for attempt in range(1, max(1, int(attempts or 1)) + 1):
            try:
                test_connection = (
                    obd_module.OBD(configured_port, fast=False, timeout=timeout, check_voltage=False)
                    if configured_port
                    else obd_module.OBD(fast=False, timeout=timeout, check_voltage=False)
                )
                if test_connection:
                    phase = str(test_connection.status() or phase)
                    if test_connection.is_connected():
                        protocol = test_connection.protocol_name() or "Unknown"
                        break

                if attempt < attempts:
                    time.sleep(retry_delay)
            except Exception as exc:
                last_exception = exc
                phase = "Connection test failed"
                if attempt < attempts:
                    time.sleep(retry_delay)
            finally:
                if test_connection and not test_connection.is_connected():
                    try:
                        test_connection.close()
                    except Exception:
                        pass
                    test_connection = None

        if test_connection and test_connection.is_connected():
            protocol = test_connection.protocol_name() or "Unknown"
            phase = str(test_connection.status() or "Car Connected")
            if get_command is not None:
                ecu_ok, ecu_detail = verify_ecu_responds(test_connection, probe_lock, get_command)
            else:
                ecu_ok = True
                ecu_detail = protocol

        adapter_responding = phase in {"ELM Connected", "OBD Connected", "Car Connected"}
        obd_protocol_seen = phase in {"OBD Connected", "Car Connected"}
        voltage = read_adapter_voltage(test_connection) if test_connection else None

        steps = [
            {
                "name": "Adapter port detected",
                "ok": any_usb_serial_present or adapter_responding,
                "detail": adapter_detail if any_usb_serial_present else phase,
            },
            {
                "name": "Adapter responding",
                "ok": adapter_responding,
                "detail": phase if adapter_responding else str(last_exception or "No ELM response yet"),
            },
            {
                "name": "Battery voltage at the port",
                "ok": voltage is not None and voltage > 6,
                "detail": f"{voltage} V measured by the adapter" if voltage is not None else "Adapter reported no voltage",
            },
            {
                "name": "OBD protocol detected",
                "ok": obd_protocol_seen,
                "detail": protocol if obd_protocol_seen else "Adapter found, waiting for vehicle protocol / ignition",
            },
            {
                "name": "ECU responding",
                "ok": ecu_ok,
                "detail": ecu_detail,
            },
        ]

        return {
            "success": ecu_ok,
            "phase": phase,
            "protocol": protocol,
            "adapter_voltage": voltage,
            "steps": steps,
        }
    except Exception as exc:
        return {
            "success": False,
            "phase": "Connection test failed",
            "protocol": protocol,
            "steps": [
                {"name": "Adapter port detected", "ok": any_usb_serial_present, "detail": adapter_detail if any_usb_serial_present else str(exc)},
                {"name": "Adapter responding", "ok": False, "detail": str(exc)},
                {"name": "OBD protocol detected", "ok": False, "detail": "Not available"},
                {"name": "ECU responding", "ok": False, "detail": "No ECU response"},
            ],
        }
    finally:
        try:
            if test_connection:
                test_connection.close()
        except Exception:
            pass


def get_readiness_snapshot(connection, lock, get_command):
    status_command = get_command("STATUS")
    status_value = _safe_query(connection, lock, status_command)

    if status_value is None:
        return {
            "available": False,
            "mil": None,
            "dtc_count": None,
            "ignition_type": "",
            "monitors": [],
        }

    monitors = []
    for name in READINESS_ORDER:
        test = getattr(status_value, name, None)
        if test is None or getattr(test, "name", "") == "":
            continue
        monitors.append({
            "name": name.replace("_", " ").title(),
            "available": bool(getattr(test, "available", False)),
            "complete": bool(getattr(test, "complete", False)),
        })

    return {
        "available": True,
        "mil": bool(getattr(status_value, "MIL", False)),
        "dtc_count": int(getattr(status_value, "DTC_count", 0)),
        "ignition_type": str(getattr(status_value, "ignition_type", "")),
        "monitors": monitors,
    }


def get_freeze_frame_snapshot(connection, lock, get_command):
    if connection is None or not connection.is_connected():
        return {"available": False, "values": {}}

    values = {}
    found = False

    for key, command_name in FREEZE_FRAME_COMMANDS.items():
        command = get_command(command_name)
        value = _safe_query(connection, lock, command)
        if value is None:
            continue
        found = True
        values[key] = str(value)

    return {
        "available": found,
        "values": values,
    }
