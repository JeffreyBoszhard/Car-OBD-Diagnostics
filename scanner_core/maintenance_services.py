"""Automatic maintenance reminders derived from existing data - no separate
"add a reminder" UI. Looks for the most recent garage note that mentions a
service/oil change, reads the mileage logged with it, and estimates when the
next one is due.

"Current mileage" is taken from the best source available, in this order:

  1. the odometer read from the ECU (PID 01 A6), when the car answers it
  2. the highest mileage logged in the car journal or the fuel-up log

The first is a real reading; the second is a floor, not a true odometer, and
the result says which one was used so the number is not mistaken for
something it isn't.

This is still a heuristic, not a manufacturer service schedule: intervals
vary a lot by car, oil type and driving style, so it is presented as an
estimate.
"""
import re

from config import OIL_CHANGE_INTERVAL_KM
from scanner_core import app_state
from scanner_core.extended_pid_services import get_known_odometer_km
from scanner_core.scan_payload_services import get_recent_garage_notes
from scanner_core.storage_services import (
    get_highest_fuel_log_mileage_km,
    get_highest_trip_mileage_km,
)
from scanner_core.units_services import KM_TO_MI, number_from_value

SERVICE_KEYWORDS = ("oil", "olie", "service", "onderhoud", "beurt")
GARAGE_NOTE_SCAN_LIMIT = 500


def _mileage_text_to_km(text):
    value = number_from_value(text)
    if value is None:
        return None
    if re.search(r"\bmi\b", str(text), re.IGNORECASE) and "km" not in str(text).lower():
        return value / KM_TO_MI
    return value


def _latest_service_note(garage_notes):
    for note in garage_notes:
        haystack = f"{note.get('title', '')} {note.get('note', '')}".lower()
        if any(keyword in haystack for keyword in SERVICE_KEYWORDS):
            km = _mileage_text_to_km(note.get("mileage"))
            if km is not None:
                return note, km
    return None, None


def _highest_logged_mileage_km(garage_notes):
    """Highest mileage figure seen in any logged record.

    The fuel log and trip log maxima come straight from SQL rather than from
    loading every row into Python.
    """
    candidates = []

    fuel_max = get_highest_fuel_log_mileage_km(app_state.DB_PATH)
    if fuel_max is not None:
        candidates.append(fuel_max)

    trip_max = get_highest_trip_mileage_km(app_state.DB_PATH)
    if trip_max is not None:
        candidates.append(trip_max)

    for note in garage_notes:
        km = _mileage_text_to_km(note.get("mileage"))
        if km is not None:
            candidates.append(km)

    return max(candidates) if candidates else None


def get_maintenance_reminder(units):
    # with_payload=False: only the note text and mileage matter here, and
    # each note's stored scan snapshot is far larger than the note itself.
    garage_notes = get_recent_garage_notes(GARAGE_NOTE_SCAN_LIMIT, with_payload=False)
    # get_recent_garage_notes already orders newest-first.
    service_note, last_service_km = _latest_service_note(garage_notes)

    if last_service_km is None:
        return {"available": False}

    odometer_km = get_known_odometer_km()
    logged_km = _highest_logged_mileage_km(garage_notes)

    if odometer_km is not None:
        current_km = odometer_km
        mileage_source = "ecu_odometer"
    elif logged_km is not None:
        current_km = logged_km
        mileage_source = "logged_mileage"
    else:
        current_km = last_service_km
        mileage_source = "last_service"

    due_at_km = last_service_km + OIL_CHANGE_INTERVAL_KM
    remaining_km = due_at_km - current_km

    def display(value_km):
        value = value_km * KM_TO_MI if units == "imperial" else value_km
        return round(value)

    return {
        "available": True,
        "last_service_at": display(last_service_km),
        "due_at": display(due_at_km),
        "current": display(current_km),
        "remaining": display(remaining_km),
        "overdue": remaining_km <= 0,
        "distance_unit": "mi" if units == "imperial" else "km",
        "interval": display(OIL_CHANGE_INTERVAL_KM),
        "mileage_source": mileage_source,
        "mileage_source_label": {
            "ecu_odometer": "Odometer read from the ECU",
            "logged_mileage": "Highest mileage you logged",
            "last_service": "Last service entry",
        }[mileage_source],
        "last_service_title": service_note.get("title") if service_note else "",
        "last_service_date": service_note.get("created_at") if service_note else "",
    }
