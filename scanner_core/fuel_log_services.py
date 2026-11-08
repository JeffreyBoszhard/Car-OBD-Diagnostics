"""Fuel-up log: mileage + liters filled at each stop, stored in km/liters
internally regardless of the unit system the user filled the form in, with
a per-fill economy trend computed from the mileage delta since the
previous (older) fill-up."""
import time

from scanner_core import app_state
from scanner_core.storage_services import (
    delete_fuel_log as storage_delete_fuel_log,
    get_recent_fuel_logs as storage_get_recent_fuel_logs,
    save_fuel_log as storage_save_fuel_log,
)
from scanner_core.units_services import (
    distance_to_km,
    km_to_display_distance,
    liters_to_display_volume,
    volume_to_liters,
)

FUEL_LOG_LIMIT = 200


def _economy_l_per_100km(liters, distance_km):
    if distance_km <= 0:
        return None
    return liters / distance_km * 100.0


def _format_economy(l_per_100km, units):
    if l_per_100km is None or l_per_100km <= 0:
        return None
    if units == "imperial":
        return {"value": round(235.215 / l_per_100km, 1), "unit": "mpg"}
    return {"value": round(l_per_100km, 1), "unit": "L/100km"}


def save_fuel_log_entry(mileage, liters, cost, notes, units, vehicle_id=None):
    mileage_km = distance_to_km(float(mileage), units)
    liters_value = volume_to_liters(float(liters), units)
    cost_value = float(cost) if cost not in (None, "") else None
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    notes = str(notes or "").strip()

    return storage_save_fuel_log(
        app_state.DB_PATH, created_at, mileage_km, liters_value, cost_value, notes, vehicle_id
    )


def delete_fuel_log_entry(log_id):
    return storage_delete_fuel_log(app_state.DB_PATH, int(log_id))


def get_fuel_log_history(units, vehicle_id=None):
    rows = storage_get_recent_fuel_logs(app_state.DB_PATH, FUEL_LOG_LIMIT, vehicle_id)

    # rows arrive newest-mileage-first; walk oldest-to-newest to compute each
    # fill's economy against the fill before it, then re-reverse for display.
    chronological = list(reversed(rows))
    entries = []
    trend = []
    previous_mileage_km = None

    for row in chronological:
        mileage_km = row["mileage_km"]
        liters = row["liters"]
        l_per_100km = None
        if previous_mileage_km is not None:
            l_per_100km = _economy_l_per_100km(liters, mileage_km - previous_mileage_km)

        entry = {
            "id": row["id"],
            "created_at": row["created_at"],
            "mileage": round(km_to_display_distance(mileage_km, units), 1),
            "distance_unit": "mi" if units == "imperial" else "km",
            "volume": round(liters_to_display_volume(liters, units), 2),
            "volume_unit": "gal" if units == "imperial" else "L",
            "cost": row["cost"],
            "notes": row["notes"],
            "economy": _format_economy(l_per_100km, units),
            "vehicle_id": row.get("vehicle_id"),
        }
        entries.append(entry)
        if l_per_100km is not None:
            trend.append({"mileage": entry["mileage"], "economy": _format_economy(l_per_100km, units)["value"]})

        previous_mileage_km = mileage_km

    entries.reverse()
    return {"entries": entries, "trend": trend}
