# Made by Boszhard Development
"""Dashboard changelog entries and helpers.

User note: do not remove this file. The scanner reads these entries to show the in-app changelog popup after an update. Add only user-facing changes here, newest version first.
"""
CHANGELOG = [
    {
        "version": "v0.6.1",
        "date": "2026-08-01",
        "changes": [
            "Demo Fault Randomizer",
            "Fault Present demo scans now return a randomized number of demo faults each time.",
            "Fault Present always returns at least 2 faults and can now return up to 9 faults in one scan.",
            "Random demo faults are selected from the 100-code demo fault pool.",
            "Stored, pending and permanent demo fault groups now vary based on the random fault count.",
            "Live Sensor Stream Polish",
            "Improved live sensor card sizing and spacing so labels, badges and values stay readable instead of collapsing or stacking vertically.",
            "Improved warning sensor cards for better spacing between WARN badges, SNSR badges and values.",
            "Shortened sensor and navigation labels now show their full text when hovered.",
            "Freeze-frame values now use the same red warning styling when captured values cross configured limits.",
            "Improved the sidebar scanner identity card with clearer OBD scanner branding and ready status.",
            "Rebuilt the home launcher into a larger centered 3 by 3 tablet menu so all scanner modules line up evenly.",
            "Removed the large home header bar and pinned the version badge to the bottom-right corner.",
            "Centered the home launcher grid vertically and horizontally on the tablet screen.",
            "Aligned the home version badge with the bottom credit line and pinned it flush to the right edge.",
            "Added the current app version to the sidebar OBD identity tile.",
            "Fixed the sidebar OBD identity tile so the version text is visible beside the OBD mark.",
            "Improved sidebar version alignment so it sits centered beside the OBD mark.",
            "Live Graph Hover Values",
            "Live dashboard graphs now show the nearest value when hovering over the line.",
            "Exported graph playback HTML now also shows hover values on every chart.",
            "Fault Code Layout",
            "Rebalanced fault code card height and spacing so large code lists stay readable without text overlap.",
            "Saved Scan History",
            "Added delete controls for saved scan history entries.",
            "Deleting a saved scan removes it from the active database backend and refreshes the history list.",
            "Setup and Storage",
            "Added language selection to the first dashboard setup popup.",
            "MySQL setup now validates the connection and creates the required scan and garage note tables before saving.",
            "Saved scans and garage notes now use MySQL when MySQL storage is selected.",
        ],
    },
    {
        "version": "v0.6.0",
        "date": "2026-08-01",
        "changes": [
            "Workshop Scanner UI",
            "Rebuilt the dashboard into a professional OBD workshop scanner layout.",
            "Redesigned every main page with cleaner placement, larger controls and a tablet-style scanner interface.",
            "Changed the sidebar to a white card-based navigation panel with smoother spacing and shadows.",
            "Unified buttons, dropdowns, cards, panels, modals and action states into one consistent rounded scanner style.",
            "Improved responsive layouts for desktop, tablet and mobile screens.",
            "Live Limit Warnings",
            "Added smooth red blinking warnings when important live values go over configured limits.",
            "Coolant temperature now warns at 100 C and higher by default.",
            "Added default warning thresholds for coolant temperature, oil temperature, intake temperature, ECU voltage, engine load, throttle position and fuel trims.",
            "Quick metric cards now turn into a smooth red warning state when a value is too high or unsafe.",
            "Live sensor rows now show a WARN badge when a value crosses its limit.",
            "Warning animations are smooth and subtle instead of harsh flashing.",
            "Warning tooltips show the limit that was triggered.",
            "Runtime Thresholds",
            "Added default warning threshold config to runtime state.",
            "Warning thresholds can be adjusted through /api/runtime.",
            "Normal dashboard behavior remains unchanged when values are inside the configured limits.",
            "Live Data Improvements",
            "Improved live graphs, quick metrics and sensor list styling for a clearer workshop scanner view.",
            "Added warning support to both English and Dutch frontend JavaScript.",
            "Fault Code Scan Improvements",
            "Added a Vehicle Diagnostic Scan progress view with stored DTC, pending DTC, permanent DTC, freeze-frame, readiness, Mode 06, vehicle info and supported PID scan steps.",
            "Fixed Cancel Scan so it no longer fails when the scan already stopped or is between scan states.",
            "Cancelling now updates the scan panel, clears scanning state and re-enables the Scan Codes button.",
            "Fault Code Reading",
            "The app reads stored, pending and permanent standard OBD-II diagnostic trouble codes where supported by the vehicle ECU.",
            "Added local enhanced DTC explanations for common engine, fuel, ignition, misfire, emissions, EVAP, voltage, ECU and transmission codes.",
            "Added fallback support for generic P00-P07, body, chassis and network communication code groups.",
            "Added manufacturer-specific fallback handling for P1xxx codes.",
            "Vehicle Info, Report and History",
            "Improved Vehicle Info layout for VIN, manual VIN lookup, RDW plate lookup, decoded vehicle details and lookup history.",
            "Fixed duplicate Vehicle Info element IDs that could leave RDW plate, brand, model or fuel fields empty.",
            "Fixed long VIN overflow inside the Vehicle Info page.",
            "Fixed report export preset handling so Full, Fault codes, Live data and Vehicle info exports use the selected mode.",
            "Fixed saved scan history details so selecting a scan shows stored snapshot information.",
            "Service and Polling Profiles",
            "Improved Service page placement for adapter setup, connection test, SAFE mode actions, demo mode and polling profiles.",
            "Added Debug Mode as a polling profile.",
            "Debug Mode is now preserved correctly in local browser storage.",
            "Reloading the dashboard no longer falls back incorrectly when Debug Mode is selected.",
            "Improved COM port dropdown styling and placement.",
            "Garage Notes",
            "Improved Garage Notes layout for note creation, vehicle identity fields, search, export and note history.",
            "Garage notes continue to support VIN, license plate, mileage, title, note text and optional photo attachment data.",
            "Changelog and Update Notifications",
            "Added changelog popups shown after installing a new local app version.",
            "Changelog popups now show only changes newer than the last acknowledged version.",
            "Moved changelog entries into changelog.py for cleaner release management.",
            "Added dashboard update notification support for newer GitHub versions.",
            "Update notifications link to the GitHub project page for manual updating.",
            "Known Notes",
            "Standard OBD-II access is still limited to data and fault codes supported by the vehicle ECU and adapter.",
            "Manufacturer-specific modules such as ABS, airbag, BCM and ADAS may require brand-specific diagnostic tools.",
            "Automatic GitHub updating is not installed yet; the dashboard currently notifies and links to GitHub for manual updates.",
            "Use this software at your own risk and do not operate the dashboard while driving.",
            "Be careful when clearing fault codes because clearing DTCs can remove diagnostic evidence.",
            "Licensed under GNU GPLv3.",
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


















