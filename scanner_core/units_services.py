"""Metric <-> imperial conversion for every unit kind the dashboard shows.

Speed, distance, temperature and pressure readings are stored and computed
internally in metric (the units the ECU/demo data use). This module is the
single place that converts a metric value to its imperial equivalent for
display when the user has selected Imperial units, so every page (live
gauges, saved scans, exports, health checklist) stays consistent.
"""
import re

KM_TO_MI = 0.621371
KPA_TO_PSI = 0.145038
GAL_TO_L = 3.785411784

# Maps a live-data / demo-data key to the kind of physical quantity it holds,
# so a single generic pass can convert every relevant field in a vehicle
# payload at once.
KEY_UNIT_KIND = {
    "speed": "speed",
    "coolant_temp": "temperature",
    "oil_temp": "temperature",
    "intake_temp": "temperature",
    "ambient_temp": "temperature",
    "intake_pressure": "pressure",
    "fuel_pressure": "pressure",
    "barometric_pressure": "pressure",
    "distance_since_clear": "distance",
    "distance_mil": "distance",
}


def number_from_value(value):
    if value is None:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "."))
    return float(match.group(0)) if match else None


def celsius_to_fahrenheit(celsius):
    return celsius * 9 / 5 + 32


def km_to_mi(km):
    return km * KM_TO_MI


def kpa_to_psi(kpa):
    return kpa * KPA_TO_PSI


def liters_to_gal(liters):
    return liters / GAL_TO_L


def gal_to_liters(gal):
    return gal * GAL_TO_L


def distance_to_km(value, units):
    """Convert a distance the user typed in their selected unit system to km
    for internal storage."""
    if value is None:
        return None
    return value / KM_TO_MI if units == "imperial" else value


def km_to_display_distance(value_km, units):
    """Convert a km value stored internally to the user's display units."""
    if value_km is None:
        return None
    return value_km * KM_TO_MI if units == "imperial" else value_km


def volume_to_liters(value, units):
    """Convert a volume the user typed in their selected unit system to
    liters for internal storage."""
    if value is None:
        return None
    return gal_to_liters(value) if units == "imperial" else value


def liters_to_display_volume(value_l, units):
    """Convert a liters value stored internally to the user's selected
    display unit system."""
    if value_l is None:
        return None
    return liters_to_gal(value_l) if units == "imperial" else value_l


def convert_number(value, kind, units):
    """Convert a plain metric number to imperial for the given kind. Returns
    the original value unchanged when units is metric or the kind is unknown."""
    if units != "imperial" or value is None:
        return value

    if kind == "speed" or kind == "distance":
        return km_to_mi(value)
    if kind == "temperature":
        return celsius_to_fahrenheit(value)
    if kind == "pressure":
        return kpa_to_psi(value)
    return value


def unit_suffix(kind, units):
    if units == "imperial":
        return {"speed": "mph", "distance": "mi", "temperature": "F", "pressure": "psi", "volume": "gal"}.get(kind, "")
    return {"speed": "km/h", "distance": "km", "temperature": "C", "pressure": "kPa", "volume": "L"}.get(kind, "")


def convert_value_text(value, kind, units):
    """Convert a "<number> <unit>" display string (as stored on live/demo
    vehicle data) to the target unit system. Non-numeric or N/A values are
    returned unchanged."""
    if units != "imperial" or value in (None, "", "N/A"):
        return value

    match = re.match(r"^\s*(-?\d+(?:[.,]\d+)?)", str(value))
    if not match:
        return value

    number = float(match.group(1).replace(",", "."))
    converted = convert_number(number, kind, units)
    decimals = 1 if kind == "temperature" else 0
    return f"{converted:.{decimals}f} {unit_suffix(kind, units)}"


def apply_unit_conversions(vehicle, units):
    """Convert every recognised field of a live/demo vehicle dict in place
    (returns the same dict for convenience) to the selected unit system."""
    for key, kind in KEY_UNIT_KIND.items():
        item = vehicle.get(key)
        if isinstance(item, dict) and "value" in item:
            vehicle[key] = {**item, "value": convert_value_text(item["value"], kind, units)}
    return vehicle


def format_detail_number(value, kind, units, decimals=0):
    """Convert an already-parsed metric number (e.g. from number_from_value)
    to a "<value> <unit>" string in the requested unit system."""
    converted = convert_number(value, kind, units)
    return f"{converted:.{decimals}f} {unit_suffix(kind, units)}"
