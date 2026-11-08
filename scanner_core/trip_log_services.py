"""Saved trip log: a manually logged drive with a start and end odometer
reading, stored in km internally regardless of the unit system the user
filled the form in, with the driven distance computed from the mileage
delta. This is separate from the live, in-session Trip Computer in
trip_services.py - these are persisted records the user saves after a
drive, shown on the Car Journal page."""
import time

from scanner_core import app_state
from scanner_core.storage_services import (
    delete_trip as storage_delete_trip,
    get_recent_trips as storage_get_recent_trips,
    save_trip as storage_save_trip,
)
from scanner_core.units_services import distance_to_km, km_to_display_distance

TRIP_LOG_LIMIT = 200


def save_trip_log_entry(start_mileage, end_mileage, purpose, notes, units, vehicle_id=None):
    start_km = distance_to_km(float(start_mileage), units)
    end_km = distance_to_km(float(end_mileage), units)
    if end_km < start_km:
        start_km, end_km = end_km, start_km
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    purpose = str(purpose or "").strip()
    notes = str(notes or "").strip()

    return storage_save_trip(
        app_state.DB_PATH, created_at, start_km, end_km, purpose, notes, vehicle_id
    )


def delete_trip_log_entry(trip_id):
    return storage_delete_trip(app_state.DB_PATH, int(trip_id))


def get_trip_log_history(units, vehicle_id=None):
    rows = storage_get_recent_trips(app_state.DB_PATH, TRIP_LOG_LIMIT, vehicle_id)

    distance_unit = "mi" if units == "imperial" else "km"
    entries = []
    for row in rows:
        distance_km = row["end_mileage_km"] - row["start_mileage_km"]
        entries.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "start_mileage": round(km_to_display_distance(row["start_mileage_km"], units), 1),
            "end_mileage": round(km_to_display_distance(row["end_mileage_km"], units), 1),
            "distance": round(km_to_display_distance(distance_km, units), 1),
            "distance_unit": distance_unit,
            "purpose": row["purpose"],
            "notes": row["notes"],
            "vehicle_id": row.get("vehicle_id"),
        })

    return entries
