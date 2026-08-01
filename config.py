# Made by Boszhard Development
"""Configuration for the OBD Scanner.

All interval values are in seconds.

Lower interval values update the dashboard faster, but they also ask the OBD
adapter and ECU for data more often. If your adapter becomes unstable, shows
missing values, or the dashboard feels noisy, increase the intervals a little.
"""

# Version shown in the dashboard, sidebar and exported HTML reports.
# Bump this and add an entry to changelog.py whenever a change is made.
APP_VERSION = "v0.6.0"

# Changelog entries live in changelog.py. Bump APP_VERSION and add a new entry there whenever a change is made.

# Main dashboard refresh loop.
# This controls the general live-data update speed for normal sensor values.
# 0.1 = 10 times per second.
POLL_INTERVAL = 0.1

# Fast gauge refresh loop for RPM and vehicle speed.
# Keep this low if you want the main gauges to feel close to live.
# 0.05 = 20 times per second.
RPM_POLL_INTERVAL = 0.05

# OBD connection settings.
# Timeout: how long one connection attempt may wait for a response.
# Attempts: how many times the app tries before giving up.
# Retry delay: pause between connection attempts.
OBD_CONNECT_TIMEOUT = 0.6
OBD_CONNECT_ATTEMPTS = 2
OBD_CONNECT_RETRY_DELAY = 0.25

# Fast retry behavior after a failed connect attempt.
# Keeps reconnects responsive instead of waiting several seconds every cycle.
OBD_RECONNECT_FAST_DELAY = 0.75
OBD_RECONNECT_SLOW_DELAY = 2.0

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


# GitHub update check.
# When the dashboard opens, the app downloads the latest config.py from GitHub
# and compares the APP_VERSION found there with this local APP_VERSION.
# If GitHub has a newer version, the dashboard shows an update notification.
# This does not install updates automatically; it only checks and links to GitHub.

# Raw GitHub URL to the config.py file that contains the newest APP_VERSION.
UPDATE_CHECK_CONFIG_URL = "https://raw.githubusercontent.com/JeffreyBoszhard/Car-OBD-Diagnostics/main/config.py"

# Page opened when the user clicks the update notification.
UPDATE_DOWNLOAD_URL = "https://github.com/JeffreyBoszhard/Car-OBD-Diagnostics"

# Maximum time in seconds the app waits for GitHub to respond.
# 2.5 means the check may wait up to two and a half seconds.
# Lower value = dashboard gives up faster if internet/GitHub is slow.
# Higher value = more chance the update check succeeds on slow internet,
# but the request can stay open longer.
# If the timeout is reached, no update popup is shown and the app continues normally.
UPDATE_CHECK_TIMEOUT = 2.5








