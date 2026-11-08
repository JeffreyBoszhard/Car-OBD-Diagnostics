"""Single shared runtime state for the scanner.

Every module that needs live scanner state (the OBD connection, last-read
vehicle values, DTC results, background-loop counters, file paths, locks...)
imports this module and reads/writes it as `app_state.<name>`. Using the
module itself as the shared object means state changes made in one file
(for example the background poll loop) are immediately visible everywhere
else, without passing dozens of parameters around or relying on `global`.
"""
import threading
import time
from pathlib import Path

from config import LOG_DIR_NAME, POLL_INTERVAL, UPLOAD_DIR_NAME
from scanner_core.storage_services import db_path_from_file


def now_time():
    return time.strftime("%H:%M:%S")


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = db_path_from_file(PROJECT_ROOT / "app.py")
DASHBOARD_SETUP_PATH = PROJECT_ROOT / "dashboard_setup.json"
DASHBOARD_RUNTIME_PATH = PROJECT_ROOT / "dashboard_runtime.json"
UPLOAD_DIR = PROJECT_ROOT / UPLOAD_DIR_NAME
LOG_DIR = PROJECT_ROOT / LOG_DIR_NAME

# OBD connection object (obd.OBD instance) or None when disconnected.
connection = None

# Latest polled live sensor values, keyed by command key.
vehicle_data = {}

dtc_data = {
    "stored": [],
    "pending": [],
    "permanent": []
}
dtc_status = {
    "has_scan": False,
    "scanning": False,
    "last_scan": None,
    "message": "No fault code scan run yet."
}
readiness_data = {
    "available": False,
    "mil": None,
    "dtc_count": None,
    "ignition_type": "",
    "monitors": [],
}
freeze_frame_data = {
    "available": False,
    "values": {},
}
# Extended module data read with manufacturer-independent service modes:
# odometer (PID 0xA6), DPF/particulate-filter values and the hybrid/EV
# battery pack state, where the vehicle exposes them.
extended_data = {
    "odometer": None,
    "dpf": {},
    "battery_pack": {},
    "checked_at": None,
}
demo_codes_cleared = False

VEHICLE_SCAN_PART_DEFS = [
    ("stored_dtc", "Stored Diagnostic Trouble Codes"),
    ("pending_dtc", "Pending Diagnostic Trouble Codes"),
    ("permanent_dtc", "Permanent Diagnostic Trouble Codes"),
    ("freeze_frame", "Freeze Frame Data"),
    ("readiness", "Emissions Readiness Monitors"),
    ("mode06", "On-Board Monitor Test Results"),
    ("vehicle_info", "Vehicle Identification"),
    ("extended_pids", "Odometer / DPF / Battery Pack"),
    ("supported_pids", "Supported Live Data PIDs"),
]

vehicle_scan_state = {
    "active": False,
    "cancelled": False,
    "interrupted": False,
    "started_at": None,
    "finished_at": None,
    "current_index": -1,
    "parts": [
        {"id": part_id, "name": name, "status": "waiting", "detail": "", "result": None}
        for part_id, name in VEHICLE_SCAN_PART_DEFS
    ],
    "summary": None,
    "connection_error": None,
}
vehicle_scan_lock = threading.Lock()

vehicle_profile = {
    "vin": "",
    "vin_status": "idle",
    "vin_message": "VIN not loaded yet.",
    "vin_last_update": None,
    "decoded": {},
    "plate_query": "",
    "plate_status": "idle",
    "plate_message": "No plate lookup yet.",
    "plate_last_update": None,
    "rdw": {}
}

obd_status = {
    "connected": False,
    "protocol": "Unknown",
    "error": None,
    "user_message": "Scanner starting up.",
    "last_update": None,
    "last_successful_update": None,
    "limited_mode": False,
    "current_port": None,
    "connecting": False,
    "demo_mode": False,
    "adapter_name": "",
    "adapter_voltage": None,
    "ecu_verified": False,
    "connect_attempts": 0,
    "connection_hint": {
        "kind": "searching",
        "label": "Searching for adapter",
        "detail": "Waiting for an OBD adapter or a live ECU response.",
    },
    "poll_interval": POLL_INTERVAL,
    "poll_guard_active": False,
    "poll_guard_reason": "",
    "recent_errors": []
}

state_lock = threading.Lock()
obd_lock = threading.Lock()
connect_lock = threading.Lock()
vin_refresh_lock = threading.Lock()
vin_refresh_in_progress = False
vin_autoload_attempted = False
query_error_streak = 0
# Consecutive failed connection rounds, used to back the reconnect loop off
# when no adapter is plugged in at all.
connect_failure_streak = 0
current_live_poll_interval = POLL_INTERVAL
demo_drive_state = {
    "speed_kmh": 0.0,
    "preset": "idle",
}
error_log_state = {}

# Running trip-computer totals since the last reset (app start, manual
# reset, or a demo-mode toggle). Mutated in place by trip_services.
trip_state = {
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
}
