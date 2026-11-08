"""Dashboard setup (language + units) and runtime state (warning thresholds,
fuel-trim history), both stored as small JSON files next to app.py, plus the
GitHub version-check helpers behind the update notification.

Reads go through an mtime-aware cache: the live-data stream asks for both
files on every frame, and re-reading and re-parsing them each time was pure
waste. Writes are atomic (write to a temporary file, then replace), so a
crash or a power cut during a save can no longer leave a half-written
dashboard_setup.json that resets the app to its first-run wizard.
"""
import json
import os
import re
import threading
import time
from urllib.request import Request, urlopen

from config import (
    JSON_STATE_CACHE_TTL,
    UPDATE_CHECK_CACHE_TTL,
    UPDATE_CHECK_CONFIG_URL,
    UPDATE_CHECK_TIMEOUT,
)
from scanner_core import app_state
from scanner_core.error_services import log_error
from scanner_core.translation import DEFAULT_UNITS

DEFAULT_SETUP = {
    "completed": False,
    "language": "en",
    "units": DEFAULT_UNITS,
    "created_at": "",
    "updated_at": "",
}

DEFAULT_WARNING_THRESHOLDS = {
    "coolant_temp_c": 100,
    "oil_temp_c": 125,
    "intake_temp_c": 70,
    "ecu_voltage_low_v": 11.8,
    "ecu_voltage_high_v": 15.0,
    "engine_load_pct": 90,
    "throttle_pct": 90,
    "fuel_trim_abs_pct": 12,
}

DEFAULT_RUNTIME = {
    "warning_thresholds": DEFAULT_WARNING_THRESHOLDS,
    "fuel_trim_history": [],
}

_state_lock = threading.Lock()
# path -> {"data": dict, "mtime": float, "checked_at": float}
_json_cache = {}

_update_check_lock = threading.Lock()
_update_check_cache = {"checked_at": 0.0, "version": ""}


def parse_version_tuple(value):
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:3]) if parts else (0, 0, 0)


def is_newer_version(latest, current):
    latest_tuple = parse_version_tuple(latest)
    current_tuple = parse_version_tuple(current)
    length = max(len(latest_tuple), len(current_tuple), 3)
    latest_tuple += (0,) * (length - len(latest_tuple))
    current_tuple += (0,) * (length - len(current_tuple))
    return latest_tuple > current_tuple


def fetch_latest_github_version(force=False):
    """Read APP_VERSION out of the config.py published on GitHub.

    The answer is cached: the dashboard checks for updates on every page load
    and a repository tag does not change by the second.
    """
    if not UPDATE_CHECK_CONFIG_URL:
        return ""

    now = time.monotonic()
    with _update_check_lock:
        age = now - _update_check_cache["checked_at"]
        if not force and _update_check_cache["checked_at"] and age < UPDATE_CHECK_CACHE_TTL:
            return _update_check_cache["version"]

    request_obj = Request(
        UPDATE_CHECK_CONFIG_URL,
        headers={
            "User-Agent": "OBD-Scanner-Update-Check",
            "Accept": "text/plain",
        },
    )
    with urlopen(request_obj, timeout=UPDATE_CHECK_TIMEOUT) as response:
        content = response.read().decode("utf-8", errors="replace")

    match = re.search(r'''APP_VERSION\s*=\s*["']([^"']+)["']''', content)
    version = match.group(1).strip() if match else ""

    with _update_check_lock:
        _update_check_cache["checked_at"] = now
        _update_check_cache["version"] = version
    return version


def load_json_file(path, default):
    """Read a JSON state file, reusing the cached copy while it is unchanged.

    The file's mtime is only re-checked once per JSON_STATE_CACHE_TTL, so a
    hot path that asks several times per second does not even stat the file.
    An edit made by hand is still picked up within that window.
    """
    key = str(path)
    now = time.monotonic()

    with _state_lock:
        entry = _json_cache.get(key)
        if entry and (now - entry["checked_at"]) < JSON_STATE_CACHE_TTL:
            return {**default, **entry["data"]}

    data = {}
    mtime = 0.0
    try:
        if path.exists():
            mtime = path.stat().st_mtime
            with _state_lock:
                entry = _json_cache.get(key)
                if entry and entry["mtime"] == mtime:
                    entry["checked_at"] = now
                    return {**default, **entry["data"]}

            with path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
    except Exception as e:
        log_error("Read JSON state", e)
        return dict(default)

    with _state_lock:
        _json_cache[key] = {"data": data, "mtime": mtime, "checked_at": now}
    return {**default, **data}


def save_json_file(path, payload):
    """Write a JSON state file atomically and refresh its cache entry."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception as e:
        log_error("Save JSON state", e)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return payload

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    with _state_lock:
        _json_cache[str(path)] = {
            "data": payload,
            "mtime": mtime,
            "checked_at": time.monotonic(),
        }
    return payload


def get_dashboard_setup():
    return load_json_file(app_state.DASHBOARD_SETUP_PATH, DEFAULT_SETUP)


def get_runtime_state():
    state = load_json_file(app_state.DASHBOARD_RUNTIME_PATH, DEFAULT_RUNTIME)
    # A partially filled threshold block should still expose every key, so
    # callers can read a threshold without defaulting at every use site.
    state["warning_thresholds"] = {
        **DEFAULT_WARNING_THRESHOLDS,
        **(state.get("warning_thresholds") or {}),
    }
    return state


def get_warning_thresholds():
    return get_runtime_state()["warning_thresholds"]


def threshold(name, fallback=None):
    value = get_warning_thresholds().get(name, fallback)
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def infer_obd_bus_mode(protocol):
    text = str(protocol or "").lower()
    if "simulator" in text:
        return {"mode": "Demo", "confidence": "high", "detail": "Demo mode is active."}
    if "can" in text or "iso 15765" in text:
        if "500" in text or "15765" in text:
            return {"mode": "HS-CAN", "confidence": "medium", "detail": "Standard OBD-II CAN normally runs on the high-speed CAN pair."}
        if "125" in text or "ms" in text:
            return {"mode": "MS-CAN", "confidence": "medium", "detail": "Protocol text hints at medium-speed CAN."}
        return {"mode": "HS-CAN", "confidence": "low", "detail": "Generic ELM adapters expose standard OBD-II CAN; true MS-CAN usually needs a switchable/STN adapter."}
    if "iso 9141" in text or "kwp" in text or "iso 14230" in text:
        return {"mode": "K-Line", "confidence": "medium", "detail": "Pre-CAN ISO 9141-2 / KWP2000 serial bus."}
    if "j1850" in text:
        return {"mode": "J1850", "confidence": "medium", "detail": "Older SAE J1850 PWM/VPW bus."}
    return {"mode": "Unknown", "confidence": "low", "detail": "No CAN protocol detected yet."}
