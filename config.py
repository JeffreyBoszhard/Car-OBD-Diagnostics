"""Configuration for the OBD Scanner.

User note: this file controls scanner version, polling speed, OBD connection
behavior, update checking and external lookup endpoints. Change values
carefully, restart the scanner after editing, and keep APP_VERSION aligned
with changelog.py when making a real release.

All interval values are in seconds.

Lower interval values update the dashboard faster, but they also ask the OBD
adapter and ECU for data more often. If your adapter becomes unstable, shows
missing values, or the dashboard feels noisy, increase the intervals a little.
"""
import os


def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default):
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


# Version shown in the dashboard, sidebar and exported HTML reports.
# Bump this and add an entry to changelog.py whenever a change is made.
APP_VERSION = "v0.8.1"

# Changelog entries live in changelog.py. Bump APP_VERSION and add a new entry
# there whenever a change is made.

# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
# Host/port the dashboard listens on. 0.0.0.0 makes it reachable from other
# devices on the same network (a workshop tablet or phone); 127.0.0.1 keeps it
# on this machine only.
# OBD_HTTP_PORT, not OBD_PORT: "obd port" already means the serial/adapter
# port everywhere else in this app, and reusing the name invites a confusing
# mistake.
SERVER_HOST = os.environ.get("OBD_HOST", "0.0.0.0")
SERVER_PORT = _env_int("OBD_HTTP_PORT", 5000)

# Flask debug mode. This MUST stay off for anything reachable over a network:
# the Werkzeug debugger allows running arbitrary code on this machine. Enable
# it only on a trusted development machine with OBD_DEBUG=1.
DEBUG = _env_flag("OBD_DEBUG", False)

# Optional PIN lock. When set (OBD_PIN=1234 or by editing this value), the
# dashboard asks for this PIN once per browser before anything is shown.
# Leave empty to keep the dashboard open to everyone on the network.
ACCESS_PIN = os.environ.get("OBD_PIN", "").strip()

# How long a successful PIN unlock stays valid, in seconds (default 30 days).
ACCESS_SESSION_MAX_AGE = _env_int("OBD_PIN_MAX_AGE", 60 * 60 * 24 * 30)

# Secret used to sign the unlock cookie. A random value is generated per start
# when this is empty, which simply means everyone has to re-enter the PIN after
# a restart.
SECRET_KEY = os.environ.get("OBD_SECRET_KEY", "")

# ---------------------------------------------------------------------------
# Polling and live data
# ---------------------------------------------------------------------------
# Main dashboard refresh loop.
# This controls the general live-data update speed for normal sensor values.
# 0.1 = 10 times per second.
POLL_INTERVAL = 0.1

# Fast gauge refresh loop for RPM and vehicle speed.
# Keep this low if you want the main gauges to feel close to live.
# 0.05 = 20 times per second.
RPM_POLL_INTERVAL = 0.05

# How often the dashboard is pushed a fresh live-data frame over the
# server-sent events stream (/api/stream). The browser no longer polls
# /api/data in a loop, so this is what sets the on-screen update rate.
STREAM_INTERVAL = 0.2

# How often the fast gauge channel of that same stream is pushed.
STREAM_GAUGE_INTERVAL = 0.05

# Maximum number of frames queued per connected browser before the slowest
# frames are dropped. Keeps a backgrounded tab from growing memory.
STREAM_QUEUE_SIZE = 8

# OBD connection settings.
# Timeout: how long one connection attempt may wait for a response.
# Attempts: how many times the app tries before giving up.
# Retry delay: pause between connection attempts.
OBD_CONNECT_TIMEOUT = 0.8
OBD_CONNECT_ATTEMPTS = 2
OBD_CONNECT_RETRY_DELAY = 0.25

# Fast retry behavior after a failed connect attempt.
# Keeps reconnects responsive instead of waiting several seconds every cycle.
OBD_RECONNECT_FAST_DELAY = 0.75
OBD_RECONNECT_SLOW_DELAY = 2.0

# After this many consecutive failed connection rounds the app slows its
# reconnect attempts down to OBD_RECONNECT_IDLE_DELAY, so a dashboard left
# open without an adapter stops hammering the serial ports.
OBD_RECONNECT_BACKOFF_AFTER = 6
OBD_RECONNECT_IDLE_DELAY = 6.0

# Verify a new connection by actually reading a PID before the app reports
# "connected". An ELM327 clone can report a happy serial link while the ECU
# answers nothing, which used to show up as a connected scanner with only
# N/A values.
OBD_VERIFY_ECU_ON_CONNECT = True

# Candidate baud rates tried when a port does not answer at the adapter's
# auto-detected rate. Most ELM327 clones are 38400 or 9600; STN/OBDLink
# adapters often run faster.
OBD_BAUD_CANDIDATES = (38400, 9600, 115200, 500000)

# Maximum live-data delay used by the poll guard.
# If repeated OBD query errors happen, the app slows polling down up to this
# value to give weak adapters or slow ECUs more breathing room.
MAX_POLL_INTERVAL = 0.8

# Sensor priority intervals.
# Fast sensors are useful while driving and can update often.
# Medium sensors are useful, but do not need millisecond updates.
# Slow sensors are non-critical values such as fuel level or counters.
FAST_SENSOR_INTERVAL = 0.5
MEDIUM_SENSOR_INTERVAL = 2.0
SLOW_SENSOR_INTERVAL = 10.0

# Stale-data threshold for fast values.
# If a fast value has not updated within this time, the UI may treat it as old.
STALE_AFTER_SECONDS = 0.9

# Number of saved scans and garage notes shown in history lists.
SCAN_HISTORY_LIMIT = 20

# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
# The dashboard asks for status and live data many times per second. These
# TTLs keep that from turning into a database read, two JSON file reads and a
# full serial-port enumeration on every single request.
SETTINGS_CACHE_TTL = 2.0
JSON_STATE_CACHE_TTL = 2.0
SERIAL_PORT_CACHE_TTL = 2.0
PID_SUPPORT_CACHE_TTL = 30.0
MODE06_CACHE_TTL = 20.0

# How often the fuel-trim history is appended to dashboard_runtime.json.
# This used to happen on every single live-data request, which rewrote the
# whole file about ten times per second.
FUEL_TRIM_SAMPLE_INTERVAL = 10.0
FUEL_TRIM_HISTORY_LIMIT = 500

# ---------------------------------------------------------------------------
# Photo attachments
# ---------------------------------------------------------------------------
# Garage-note photos are stored as files in this folder and referenced by
# name, instead of being embedded in the saved scan payload.
UPLOAD_DIR_NAME = "uploads"
UPLOAD_MAX_BYTES = 6 * 1024 * 1024
UPLOAD_ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# ---------------------------------------------------------------------------
# Error log file
# ---------------------------------------------------------------------------
# Errors are kept in memory for the System page and appended to this file so
# a problem that happened an hour ago can still be looked up.
LOG_DIR_NAME = "logs"
ERROR_LOG_FILENAME = "scanner-errors.log"
ERROR_LOG_MAX_BYTES = 1 * 1024 * 1024
ERROR_LOG_BACKUP_COUNT = 3
RECENT_ERROR_MEMORY_LIMIT = 40

# ---------------------------------------------------------------------------
# Maintenance estimates
# ---------------------------------------------------------------------------
# Generic oil/service interval used when no better information is available.
OIL_CHANGE_INTERVAL_KM = 10000

# ---------------------------------------------------------------------------
# GitHub update check
# ---------------------------------------------------------------------------
# When the dashboard opens, the app downloads the latest config.py from GitHub
# and compares the APP_VERSION found there with this local APP_VERSION.
# If GitHub has a newer version, the dashboard shows an update notification.
# This does not install updates automatically; it only checks and links to
# GitHub.

# Raw GitHub URL to the config.py file that contains the newest APP_VERSION.
UPDATE_CHECK_CONFIG_URL = "https://raw.githubusercontent.com/JeffreyBoszhard/Car-OBD-Diagnostics/main/config.py"

# Page opened when the user clicks the update notification.
UPDATE_DOWNLOAD_URL = "https://github.com/JeffreyBoszhard/Car-OBD-Diagnostics"

# Maximum time in seconds the app waits for GitHub to respond.
# 2.5 means the check may wait up to two and a half seconds.
# Lower value = dashboard gives up faster if internet/GitHub is slow.
# Higher value = more chance the update check succeeds on slow internet,
# but the request can stay open longer.
# If the timeout is reached, no update popup is shown and the app continues
# normally.
UPDATE_CHECK_TIMEOUT = 2.5

# How long a completed update check is reused before GitHub is asked again.
UPDATE_CHECK_CACHE_TTL = 900.0
