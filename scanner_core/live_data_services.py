"""Live sensor polling helpers: building/staleness-tracking of individual
live values, the adapter poll-guard (backing off polling speed after
repeated query errors) and picking which commands are active for the
current poll profile."""
import time

from config import MAX_POLL_INTERVAL, POLL_INTERVAL, STALE_AFTER_SECONDS
from scanner_core import app_state
from scanner_core.obd_bridge import get_command
from scanner_core.settings_services import get_poll_profile

LIVE_COMMANDS = {
    "status": ("Monitor status", get_command("STATUS")),
    "fuel_status": ("Fuel system", get_command("FUEL_STATUS")),
    "speed": ("Speed", get_command("SPEED")),
    "warmups_since_clear": ("Warmups since codes cleared", get_command("WARMUPS_SINCE_DTC_CLEAR")),
    "distance_since_clear": ("Distance since codes cleared", get_command("DISTANCE_SINCE_DTC_CLEAR")),
    "time_since_clear": ("Time since codes cleared", get_command("TIME_SINCE_DTC_CLEARED")),
    "coolant_temp": ("Coolant temperature", get_command("COOLANT_TEMP")),
    "oil_temp": ("Oil temperature", get_command("OIL_TEMP")),
    "intake_temp": ("Intake air temperature", get_command("INTAKE_TEMP")),
    "ambient_temp": ("Ambient air temperature", get_command("AMBIANT_AIR_TEMP")),
    "engine_load": ("Engine load", get_command("ENGINE_LOAD")),
    "throttle": ("Throttle position", get_command("THROTTLE_POS")),
    "intake_pressure": ("Intake manifold pressure", get_command("INTAKE_PRESSURE")),
    "fuel_pressure": ("Fuel pressure", get_command("FUEL_PRESSURE")),
    "barometric_pressure": ("Barometric pressure", get_command("BAROMETRIC_PRESSURE")),
    "timing_advance": ("Timing advance", get_command("TIMING_ADVANCE")),
    "short_fuel_trim_1": ("Short fuel trim bank 1", get_command("SHORT_FUEL_TRIM_1")),
    "long_fuel_trim_1": ("Long fuel trim bank 1", get_command("LONG_FUEL_TRIM_1")),
    "o2_b1s1": ("O2 sensor B1S1", get_command("O2_B1S1")),
    "o2_b1s2": ("O2 sensor B1S2", get_command("O2_B1S2")),
    "maf": ("MAF air flow", get_command("MAF")),
    "fuel_level": ("Fuel level", get_command("FUEL_LEVEL")),
    "runtime": ("Engine runtime", get_command("RUN_TIME")),
    "distance_mil": ("Distance with MIL on", get_command("DISTANCE_W_MIL")),
    "control_voltage": ("ECU voltage", get_command("CONTROL_MODULE_VOLTAGE")),
    "voltage": ("Adapter voltage", get_command("ELM_VOLTAGE")),
}
LIMITED_MODE_COMMAND_KEYS = {
    "speed",
    "coolant_temp",
    "engine_load",
    "throttle",
    "long_fuel_trim_1",
    "control_voltage",
}
RPM_COMMAND = get_command("RPM")
SPEED_COMMAND = get_command("SPEED")
ENGINE_LOAD_COMMAND = get_command("ENGINE_LOAD")
THROTTLE_COMMAND = get_command("THROTTLE_POS")
FAST_LOOP_COMMAND_KEYS = {"speed", "engine_load", "throttle"}


def set_vehicle_value(key, label, value):
    measured_at = time.time()
    with app_state.state_lock:
        previous = dict(app_state.vehicle_data.get(key, {}))
        app_state.vehicle_data[key] = build_live_item(previous, label, value, measured_at)


def build_live_item(previous, label, value, measured_at=None):
    measured_at = measured_at or time.time()
    previous = previous or {}
    is_fresh = value not in {None, "", "N/A"}

    if is_fresh:
        updated_epoch = measured_at
        display_value = value
    else:
        updated_epoch = previous.get("updated_epoch")
        display_value = previous.get("value", "N/A")

    age_seconds = None if updated_epoch is None else max(0.0, measured_at - updated_epoch)
    stale = bool(updated_epoch and age_seconds is not None and age_seconds >= STALE_AFTER_SECONDS)

    return {
        "label": label,
        "value": display_value,
        "updated_at": time.strftime("%H:%M:%S", time.localtime(updated_epoch)) if updated_epoch else "--",
        "updated_epoch": updated_epoch,
        "stale": stale if display_value != "N/A" else False,
    }


def refresh_vehicle_stale_flags():
    now = time.time()

    with app_state.state_lock:
        for key, item in list(app_state.vehicle_data.items()):
            updated_epoch = item.get("updated_epoch")
            app_state.vehicle_data[key] = {
                **item,
                "stale": bool(updated_epoch and (now - updated_epoch) >= STALE_AFTER_SECONDS),
            }


def apply_poll_guard_success():

    if app_state.query_error_streak > 0:
        app_state.query_error_streak -= 1

    if app_state.query_error_streak == 0:
        app_state.current_live_poll_interval = POLL_INTERVAL

    with app_state.state_lock:
        app_state.obd_status["poll_interval"] = round(app_state.current_live_poll_interval, 2)
        app_state.obd_status["poll_guard_active"] = app_state.current_live_poll_interval > POLL_INTERVAL
        app_state.obd_status["poll_guard_reason"] = (
            "Polling slowed temporarily because the adapter returned repeated query errors."
            if app_state.current_live_poll_interval > POLL_INTERVAL
            else ""
        )


def apply_poll_guard_error():

    app_state.query_error_streak += 1
    if app_state.query_error_streak >= 3:
        app_state.current_live_poll_interval = min(MAX_POLL_INTERVAL, round(POLL_INTERVAL + min(0.6, app_state.query_error_streak * 0.05), 2))

    with app_state.state_lock:
        app_state.obd_status["poll_interval"] = round(app_state.current_live_poll_interval, 2)
        app_state.obd_status["poll_guard_active"] = app_state.current_live_poll_interval > POLL_INTERVAL
        app_state.obd_status["poll_guard_reason"] = (
            "Polling slowed temporarily because the adapter returned repeated query errors."
            if app_state.current_live_poll_interval > POLL_INTERVAL
            else ""
        )


def get_active_live_commands():
    commands = {key: value for key, value in LIVE_COMMANDS.items() if key not in FAST_LOOP_COMMAND_KEYS}
    if get_poll_profile()["limited"]:
        return {
            key: value
            for key, value in commands.items()
            if key in LIMITED_MODE_COMMAND_KEYS
        }
    return commands
