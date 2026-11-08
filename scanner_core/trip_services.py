"""Live trip computer: session distance, idle/driving time and a MAF-based
fuel economy estimate, derived entirely from values already being polled
(speed, RPM, coolant, MAF) - no extra OBD queries needed.

The fuel numbers are an estimate, not a certified reading: they use the
standard MAF-based approximation most OBD trip-computer apps use, assuming
gasoline with a stoichiometric air/fuel ratio of 14.7:1 and a fuel density
of about 750 g/L.
"""
import time

from scanner_core import app_state
from scanner_core.units_services import convert_number

_AFR = 14.7
_FUEL_DENSITY_G_PER_L = 750.0
_MOVING_SPEED_THRESHOLD_KMH = 2.0
_RUNNING_RPM_THRESHOLD = 300
_MAX_STEP_SECONDS = 2.0

# Minimum *new* distance since the last auto-save before another one is
# allowed. Keeps a flaky adapter that drops and reconnects every few
# minutes (see the CH340/xhci disconnect issue) from spamming the saved
# scan history with near-empty snapshots every time the link blips.
_AUTOSAVE_MIN_NEW_DISTANCE_KM = 0.5

_DEFAULT_TRIP_STATE = {
    "session_started_at": None,
    "distance_km": 0.0,
    "fuel_used_l": 0.0,
    "driving_seconds": 0.0,
    "idle_seconds": 0.0,
    "max_speed_kmh": 0.0,
    "max_rpm": 0,
    "coolant_sum_c": 0.0,
    "coolant_samples": 0,
    "last_update_epoch": None,
    "last_autosave_distance_km": 0.0,
}


def reset_trip_state():
    with app_state.state_lock:
        app_state.trip_state.clear()
        app_state.trip_state.update(_DEFAULT_TRIP_STATE)
        app_state.trip_state["session_started_at"] = time.time()


def _fuel_l_per_hour(maf_g_s):
    return maf_g_s * 3600.0 / (_AFR * _FUEL_DENSITY_G_PER_L)


def update_trip_state(speed_kmh, rpm, coolant_c, maf_g_s):
    now = time.time()
    with app_state.state_lock:
        state = app_state.trip_state
        if state.get("session_started_at") is None:
            state["session_started_at"] = now

        last = state.get("last_update_epoch")
        state["last_update_epoch"] = now
        if last is None:
            return
        dt = max(0.0, min(_MAX_STEP_SECONDS, now - last))
        if dt <= 0:
            return

        if speed_kmh is not None:
            state["distance_km"] += speed_kmh * dt / 3600.0
            state["max_speed_kmh"] = max(state["max_speed_kmh"], speed_kmh)

        is_running = rpm is not None and rpm > _RUNNING_RPM_THRESHOLD
        is_moving = speed_kmh is not None and speed_kmh > _MOVING_SPEED_THRESHOLD_KMH
        if is_running:
            if is_moving:
                state["driving_seconds"] += dt
            else:
                state["idle_seconds"] += dt

        if rpm is not None:
            state["max_rpm"] = max(state["max_rpm"], rpm)

        if coolant_c is not None:
            state["coolant_sum_c"] += coolant_c
            state["coolant_samples"] += 1

        if maf_g_s is not None:
            state["fuel_used_l"] += _fuel_l_per_hour(maf_g_s) * dt / 3600.0


def should_autosave_on_disconnect():
    """Call when a real (non-demo) OBD connection just dropped. Returns True
    at most once per _AUTOSAVE_MIN_NEW_DISTANCE_KM of new driving, so a
    flaky adapter reconnecting repeatedly doesn't spam the scan history."""
    with app_state.state_lock:
        state = app_state.trip_state
        distance_km = state.get("distance_km", 0.0)
        last_autosave_km = state.get("last_autosave_distance_km", 0.0)
        if distance_km - last_autosave_km < _AUTOSAVE_MIN_NEW_DISTANCE_KM:
            return False
        state["last_autosave_distance_km"] = distance_km
        return True


def get_current_fuel_rate(speed_kmh, maf_g_s, units):
    """Instantaneous consumption at the current MAF + speed, or an idle
    L/hour rate when stationary. Returns None when MAF isn't available."""
    if maf_g_s is None:
        return None

    fuel_l_per_hour = _fuel_l_per_hour(maf_g_s)

    if speed_kmh is None or speed_kmh <= 1.0:
        return {"idle": True, "value": round(fuel_l_per_hour, 2), "unit": "L/h"}

    l_per_100km = fuel_l_per_hour / speed_kmh * 100.0
    if l_per_100km <= 0:
        return None

    if units == "imperial":
        return {"idle": False, "value": round(235.215 / l_per_100km, 1), "unit": "mpg"}
    return {"idle": False, "value": round(l_per_100km, 1), "unit": "L/100km"}


def _average_consumption(distance_km, fuel_used_l, units):
    if distance_km <= 0.05 or fuel_used_l <= 0:
        return None
    l_per_100km = fuel_used_l / distance_km * 100.0
    if l_per_100km <= 0:
        return None
    if units == "imperial":
        return {"value": round(235.215 / l_per_100km, 1), "unit": "mpg"}
    return {"value": round(l_per_100km, 1), "unit": "L/100km"}


def get_trip_summary(units):
    with app_state.state_lock:
        state = dict(app_state.trip_state)

    distance_km = state.get("distance_km", 0.0)
    driving_seconds = state.get("driving_seconds", 0.0)
    idle_seconds = state.get("idle_seconds", 0.0)
    fuel_used_l = state.get("fuel_used_l", 0.0)
    max_speed_kmh = state.get("max_speed_kmh", 0.0)
    coolant_samples = state.get("coolant_samples", 0)
    avg_coolant_c = (state.get("coolant_sum_c", 0.0) / coolant_samples) if coolant_samples else None
    avg_speed_kmh = (distance_km / driving_seconds) * 3600.0 if driving_seconds > 0 else 0.0

    return {
        "session_started_at": state.get("session_started_at"),
        "distance": round(convert_number(distance_km, "distance", units), 2),
        "distance_unit": "mi" if units == "imperial" else "km",
        "avg_speed": round(convert_number(avg_speed_kmh, "speed", units), 1),
        "max_speed": round(convert_number(max_speed_kmh, "speed", units), 1),
        "speed_unit": "mph" if units == "imperial" else "km/h",
        "driving_seconds": round(driving_seconds),
        "idle_seconds": round(idle_seconds),
        "max_rpm": state.get("max_rpm", 0),
        "avg_coolant": round(convert_number(avg_coolant_c, "temperature", units), 1) if avg_coolant_c is not None else None,
        "temp_unit": "F" if units == "imperial" else "C",
        "fuel_used_l": round(fuel_used_l, 2),
        "avg_consumption": _average_consumption(distance_km, fuel_used_l, units),
    }
