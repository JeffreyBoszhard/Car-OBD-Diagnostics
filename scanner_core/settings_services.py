"""Persisted scanner settings: the SQLite key-value store, demo mode,
polling profile and demo preset selection.

Everything here reads through a small in-memory cache. The dashboard asks for
status and live data many times per second, and almost every one of those
requests wants to know the polling profile, the demo flag and the selected
port. Without the cache that was three SQLite connections per request; with
it the whole settings table is read at most once every SETTINGS_CACHE_TTL
seconds, and a write refreshes the cached value immediately so the UI never
shows a stale setting it just changed.
"""
import threading
import time

from config import SETTINGS_CACHE_TTL
from scanner_core import app_state
from scanner_core.demo_services import get_demo_default_speed, normalize_demo_preset
from scanner_core.error_services import log_error
from scanner_core.storage_services import (
    get_all_settings as storage_get_all_settings,
    init_storage,
    set_setting as storage_set_setting,
)

POLL_PROFILES = {
    "performance": {
        "label": "Performance",
        "limited": False,
        "description": "Performance uses faster polling for a more live feel.",
    },
    "balanced": {
        "label": "Balanced",
        "limited": False,
        "description": "Balanced is recommended for normal diagnostics.",
    },
    "safe": {
        "label": "Economy",
        "limited": True,
        "description": "Economy slows non-critical values and limits polling to core live data.",
    },
    "debug": {
        "label": "Debug Mode",
        "limited": False,
        "description": "Debug Mode keeps full polling enabled and preserves the selected profile after reloads.",
    },
}

_cache_lock = threading.Lock()
_cache = {}
_cache_loaded_at = 0.0


def init_config_db():
    try:
        init_storage(app_state.DB_PATH)
    except Exception as e:
        log_error("Initialize config database", e)
    invalidate_settings_cache()


def invalidate_settings_cache():
    global _cache_loaded_at
    with _cache_lock:
        _cache_loaded_at = 0.0


def _settings_snapshot():
    """Return the cached settings dict, reloading it when the TTL expired."""
    global _cache, _cache_loaded_at

    now = time.monotonic()
    with _cache_lock:
        if _cache_loaded_at and (now - _cache_loaded_at) < SETTINGS_CACHE_TTL:
            return _cache

    try:
        fresh = storage_get_all_settings(app_state.DB_PATH)
    except Exception as e:
        log_error("Read config", e)
        with _cache_lock:
            return dict(_cache)

    with _cache_lock:
        _cache = fresh
        _cache_loaded_at = now
        return _cache


def get_setting(key, default=None):
    value = _settings_snapshot().get(key)
    return default if value is None else value


def set_setting(key, value):
    try:
        storage_set_setting(app_state.DB_PATH, key, value)
    except Exception as e:
        log_error("Save config", e)
        return False

    with _cache_lock:
        _cache[key] = value
    return True


def get_configured_port():
    port = get_setting("obd_port", "")
    port = port.strip() if port else ""
    return port or None


def get_last_good_port():
    """The port the app last had a verified ECU session on.

    Tried first when auto-detecting, which makes a reconnect after an engine
    restart noticeably quicker than walking the whole port list again.
    """
    port = get_setting("last_good_port", "")
    port = port.strip() if port else ""
    return port or None


def set_last_good_port(port):
    return set_setting("last_good_port", str(port or "").strip())


def get_demo_mode_enabled():
    return str(get_setting("demo_mode", "0")).strip().lower() in {"1", "true", "yes", "on"}


def set_demo_mode_enabled(enabled):
    return set_setting("demo_mode", "1" if enabled else "0")


def normalize_poll_profile(profile):
    profile = str(profile or "balanced").strip().lower()
    return profile if profile in POLL_PROFILES else "balanced"


def get_poll_profile_name():
    return normalize_poll_profile(get_setting("poll_profile", "balanced"))


def get_poll_profile():
    name = get_poll_profile_name()
    return {
        "id": name,
        **POLL_PROFILES[name],
    }


def set_poll_profile_name(profile):
    return set_setting("poll_profile", normalize_poll_profile(profile))


def get_demo_preset_name():
    return normalize_demo_preset(get_setting("demo_preset", "idle"))


def set_demo_preset_name(preset):
    return set_setting("demo_preset", normalize_demo_preset(preset))


def apply_demo_preset_state(preset, reset_speed=True):
    preset_name = normalize_demo_preset(preset)
    default_speed = get_demo_default_speed(preset_name)
    with app_state.state_lock:
        app_state.demo_drive_state["preset"] = preset_name
        if reset_speed:
            app_state.demo_drive_state["speed_kmh"] = default_speed
    return preset_name, default_speed
