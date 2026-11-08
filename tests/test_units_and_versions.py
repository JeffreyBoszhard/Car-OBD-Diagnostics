"""Pure-function tests: unit conversion, version comparison, VIN/plate
validation and DTC enrichment.

None of these need an adapter, a car or a database, which is the point: they
are the parts of the scanner that can be checked on every commit.
"""
import pytest

from scanner_core import units_services as units
from scanner_core.garage_services import normalize_garage_plate, normalize_garage_vin, validate_garage_note_identity
from scanner_core.setup_services import is_newer_version, parse_version_tuple


# ---------------------------------------------------------------- units
def test_metric_values_pass_through_unchanged():
    assert units.convert_number(100, "speed", "metric") == 100
    assert units.convert_value_text("90 km/h", "speed", "metric") == "90 km/h"


def test_speed_converts_to_mph():
    assert units.convert_value_text("100 km/h", "speed", "imperial") == "62 mph"


def test_temperature_converts_to_fahrenheit():
    assert units.convert_value_text("100 C", "temperature", "imperial") == "212.0 F"


def test_pressure_converts_to_psi():
    assert units.convert_value_text("100 kPa", "pressure", "imperial") == "15 psi"


def test_non_numeric_values_are_left_alone():
    for value in (None, "", "N/A", "Closed loop"):
        assert units.convert_value_text(value, "speed", "imperial") == value


def test_distance_round_trip_survives_conversion():
    for units_system in ("metric", "imperial"):
        display = units.km_to_display_distance(240.0, units_system)
        assert units.distance_to_km(display, units_system) == pytest.approx(240.0)


def test_volume_round_trip_survives_conversion():
    for units_system in ("metric", "imperial"):
        display = units.liters_to_display_volume(55.0, units_system)
        assert units.volume_to_liters(display, units_system) == pytest.approx(55.0)


def test_number_from_value_handles_decimal_comma():
    assert units.number_from_value("12,4 V") == 12.4
    assert units.number_from_value("-3.5 %") == -3.5
    assert units.number_from_value("N/A") is None


def test_apply_unit_conversions_only_touches_known_keys():
    vehicle = {
        "speed": {"label": "Speed", "value": "100 km/h"},
        "rpm": {"label": "RPM", "value": "2500 rpm"},
    }
    converted = units.apply_unit_conversions(dict(vehicle), "imperial")
    assert converted["speed"]["value"] == "62 mph"
    # RPM has no imperial equivalent and must not be rescaled.
    assert converted["rpm"]["value"] == "2500 rpm"


# ------------------------------------------------------------- versions
@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v0.8.0", "v0.7.0", True),
        ("v0.7.0", "v0.8.0", False),
        ("v0.8.0", "v0.8.0", False),
        ("v0.10.0", "v0.9.0", True),   # not a string comparison
        ("v1.0.0", "v0.99.9", True),
        ("v0.8.1", "v0.8", True),
        ("", "v0.8.0", False),
    ],
)
def test_is_newer_version(latest, current, expected):
    assert is_newer_version(latest, current) is expected


def test_parse_version_tuple_ignores_prefix_and_extra_parts():
    assert parse_version_tuple("v1.2.3") == (1, 2, 3)
    assert parse_version_tuple("release-2.0") == (2, 0)
    assert parse_version_tuple("nonsense") == (0, 0, 0)


# ------------------------------------------------------------ VIN/plate
def test_vin_normalisation_strips_noise_and_rejects_invalid():
    assert normalize_garage_vin("wvw zzz 1kz aw 123456") == "WVWZZZ1KZAW123456"
    assert normalize_garage_vin("too-short") == ""


def test_plate_normalisation_removes_separators():
    assert normalize_garage_plate("12-ab-34") == "12AB34"


def test_garage_note_requires_a_usable_plate():
    ok, message = validate_garage_note_identity("", "")
    assert ok is False and "plate" in message.lower()

    ok, _ = validate_garage_note_identity("", "12AB34")
    assert ok is True


def test_garage_note_rejects_a_malformed_vin():
    ok, message = validate_garage_note_identity("ABC", "12AB34")
    assert ok is False and "17" in message

    # I, O and Q are never used in a VIN.
    ok, message = validate_garage_note_identity("WVWZZZ1KZAW12345I", "12AB34")
    assert ok is False and "I, O or Q" in message
