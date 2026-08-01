# Made by Boszhard Development
"""Dashboard changelog entries and helpers.

Add a new entry at the top whenever the app changes, then bump APP_VERSION in
config.py. The dashboard popup compares APP_VERSION with the last acknowledged
version saved in the local settings database.
"""
CHANGELOG = [
    {
        "version": "v0.6.0",
        "date": "2026-08-01",
        "changes": [
            "Complete workshop scanner UI refresh with a cleaner white sidebar, larger home launcher cards and consistent rounded buttons.",
            "Added the Vehicle Diagnostic Scan progress view with per-section status, cancel support and final summary.",
            "Improved Fault Codes, Live Data, Vehicle Info, Sensors, Report, Service, Garage, History, System and Purchase Checklist layouts.",
            "Added changelog popups that show new changes after installing a new version.",
            "Added GitHub update notification when a newer version is available.",
            "Fixed report export preset handling so Full, Fault codes, Live data and Vehicle info exports use the selected mode.",
            "Fixed saved scan history details so selecting a scan shows the stored snapshot information.",
            "Fixed Vehicle Info duplicate IDs, long VIN overflow, clipped polling buttons and multiple page clipping/spacing issues.",
            "Fixed Cancel Scan so it no longer fails when the scan already stopped or is between scan states.",
        ],
    },
    {
        "version": "v0.5.0",
        "date": "2026-07-01",
        "changes": [
            "Baseline release.",
        ],
    },
]


def parse_version_tuple(value):
    parts = []
    for chunk in str(value or "").replace("v", "").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts[:3]) if parts else (0, 0, 0)


def is_entry_newer(entry, seen_version):
    if not seen_version:
        return True
    return parse_version_tuple(entry.get("version")) > parse_version_tuple(seen_version)


def get_changelog_since(seen_version):
    entries = [entry for entry in CHANGELOG if is_entry_newer(entry, seen_version)]
    return entries or CHANGELOG[:1]
