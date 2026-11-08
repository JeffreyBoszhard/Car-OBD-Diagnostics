"""Manual, forward-only multi-vehicle (car) profiles: a nickname, required
plate, optional VIN, and optional brand/model/year you create explicitly.
Existing scans, garage notes and fuel logs are left exactly as they are
(unassigned); new garage notes and fuel logs get tagged with whichever
vehicle is picked in the form (or the "active" one as a default), and the
garage/fuel log lists can be filtered by vehicle."""
import time

from scanner_core import app_state
from scanner_core.settings_services import get_setting, set_setting
from scanner_core.storage_services import (
    delete_vehicle as storage_delete_vehicle,
    get_vehicles as storage_get_vehicles,
    save_vehicle as storage_save_vehicle,
)
from scanner_core.units_services import liters_to_display_volume, volume_to_liters

ACTIVE_VEHICLE_SETTING_KEY = "active_vehicle_id"


def get_vehicles(units="metric"):
    vehicles = storage_get_vehicles(app_state.DB_PATH)

    for vehicle in vehicles:
        vehicle["tank"] = round(liters_to_display_volume(vehicle["tank_liters"], units), 1) if vehicle.get("tank_liters") else None
        vehicle["tank_unit"] = "gal" if units == "imperial" else "L"

    return vehicles


def save_vehicle(nickname, vin, plate, brand="", model="", year="", tank=None, units="metric"):
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    nickname = str(nickname or "").strip() or "My car"
    vin = str(vin or "").strip().upper()
    plate = str(plate or "").strip().upper()
    brand = str(brand or "").strip()
    model = str(model or "").strip()
    year = str(year or "").strip()
    tank_liters = None
    if tank not in (None, ""):
        try:
            tank_liters = volume_to_liters(float(tank), units)
        except (TypeError, ValueError):
            tank_liters = None

    vehicle = storage_save_vehicle(
        app_state.DB_PATH, created_at, nickname, vin, plate, brand, model, year, tank_liters
    )

    if get_active_vehicle_id() is None:
        set_active_vehicle_id(vehicle["id"])

    return vehicle


def delete_vehicle(vehicle_id):
    deleted = storage_delete_vehicle(app_state.DB_PATH, vehicle_id)

    if deleted and get_active_vehicle_id() == int(vehicle_id):
        set_active_vehicle_id(None)

    return deleted


def get_active_vehicle_id():
    value = get_setting(ACTIVE_VEHICLE_SETTING_KEY, "")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def set_active_vehicle_id(vehicle_id):
    return set_setting(ACTIVE_VEHICLE_SETTING_KEY, str(vehicle_id) if vehicle_id else "")


def get_active_vehicle():
    active_id = get_active_vehicle_id()
    if active_id is None:
        return None
    for vehicle in get_vehicles():
        if vehicle["id"] == active_id:
            return vehicle
    return None
