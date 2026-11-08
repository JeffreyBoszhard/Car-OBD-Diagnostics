"""VIN reading/decoding and Dutch RDW plate lookups.

Covers reading the raw VIN over OBD, decoding it through the NHTSA API
(with local caching), parsing the VIN structure itself as a fallback/extra
detail source, RDW plate lookups, and the shared vehicle_profile state
those flows update."""
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from scanner_core import app_state
from scanner_core.app_state import now_time
from scanner_core.cache_services import load_vin_cache, save_vin_cache
from scanner_core.demo_services import build_demo_vehicle_profile
from scanner_core.error_services import friendly_message, log_error
from scanner_core.obd_bridge import get_command
from scanner_core.settings_services import get_demo_mode_enabled, get_setting, set_setting

VIN_PATTERN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
RDW_DATASET_URL = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"
NHTSA_DECODE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"

VIN_MODEL_YEAR_CODES = {
    "A": 1980, "B": 1981, "C": 1982, "D": 1983, "E": 1984, "F": 1985, "G": 1986, "H": 1987,
    "J": 1988, "K": 1989, "L": 1990, "M": 1991, "N": 1992, "P": 1993, "R": 1994, "S": 1995,
    "T": 1996, "V": 1997, "W": 1998, "X": 1999, "Y": 2000, "1": 2001, "2": 2002, "3": 2003,
    "4": 2004, "5": 2005, "6": 2006, "7": 2007, "8": 2008, "9": 2009,
}

VIN_COUNTRY_CODES = {
    "1": "United States",
    "2": "Canada",
    "3": "Mexico",
    "J": "Japan",
    "K": "South Korea",
    "L": "China",
    "S": "United Kingdom",
    "T": "Switzerland",
    "V": "France or Spain",
    "W": "Germany",
    "Y": "Sweden or Finland",
    "Z": "Italy",
}


def fetch_json(url):
    request_obj = Request(
        url,
        headers={
            "User-Agent": "OBD-Scanner-Pro/1.0",
            "Accept": "application/json"
        }
    )

    with urlopen(request_obj, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_vin(raw_value):
    if raw_value is None:
        return ""

    compact = re.sub(r"[^A-Za-z0-9]", "", str(raw_value).upper())
    match = VIN_PATTERN.search(compact)
    return match.group(0) if match else ""


def read_vin():
    vin_command = get_command("VIN")

    if vin_command is None:
        raise RuntimeError("VIN command is not available in python-obd.")

    if not app_state.connection or not app_state.connection.is_connected():
        raise RuntimeError("No OBD connection.")

    with app_state.obd_lock:
        response = app_state.connection.query(vin_command)

    if response.is_null() or not response.value:
        raise RuntimeError("No VIN response from vehicle.")

    vin = normalize_vin(response.value)

    if not vin:
        raise RuntimeError(f"Could not parse VIN from response: {response.value}")

    return vin


def _clean_vehicle_field(value):
    if value is None:
        return ""

    text = str(value).strip()
    if not text or text.lower() in {"0", "null", "none", "not applicable"}:
        return ""
    return text


def _first_vehicle_field(*values):
    for value in values:
        cleaned = _clean_vehicle_field(value)
        if cleaned:
            return cleaned
    return ""


def _join_vehicle_fields(*values, separator=" / "):
    parts = []
    seen = set()

    for value in values:
        cleaned = _clean_vehicle_field(value)
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        parts.append(cleaned)

    return separator.join(parts)


def _build_rdw_fuel_description(row):
    return _join_vehicle_fields(
        row.get("brandstofomschrijving"),
        row.get("brandstof_omschrijving"),
        row.get("brandstof_omschrijving_1"),
        row.get("brandstof_omschrijving_2"),
        row.get("brandstof_omschrijving_3"),
        row.get("brandstof"),
    )


def _build_vin_extra_details(item):
    detail_specs = [
        ("manufacturer", "Manufacturer"),
        ("vehicle_type", "Vehicle type"),
        ("series", "Series"),
        ("trim", "Trim"),
        ("vin_wmi", "WMI"),
        ("vin_vds", "Vehicle descriptor"),
        ("vin_vis", "Vehicle identifier"),
        ("vin_check_digit", "Check digit"),
        ("vin_year_code", "Model year code"),
        ("vin_model_year_estimate", "Estimated model year"),
        ("vin_plant_code", "Plant code"),
        ("vin_serial_number", "Serial number"),
        ("vin_country_hint", "Country hint"),
        ("fuel_type_secondary", "Secondary fuel"),
        ("transmission_style", "Transmission"),
        ("engine_model", "Engine model"),
        ("engine_power_hp", "Engine power"),
        ("engine_configuration", "Engine configuration"),
        ("drive_type", "Drive type"),
        ("doors", "Doors"),
        ("seats", "Seats"),
        ("plant_company", "Plant company"),
        ("plant_location", "Plant location"),
    ]

    details = []
    for key, label in detail_specs:
        value = _clean_vehicle_field(item.get(key))
        if value:
            details.append({
                "key": key,
                "label": label,
                "value": value,
            })
    return details


def _decode_vin_year_code(code):
    base_year = VIN_MODEL_YEAR_CODES.get(str(code or "").upper())
    if not base_year:
        return ""

    current_year = time.localtime().tm_year + 1
    candidate = base_year
    while candidate + 30 <= current_year:
        candidate += 30
    return str(candidate)


def _decode_vin_structure(vin):
    normalized = normalize_vin(vin)
    if len(normalized) != 17:
        return {}

    wmi = normalized[:3]
    vds = normalized[3:9]
    vis = normalized[9:]
    year_code = normalized[9]
    plant_code = normalized[10]
    serial_number = normalized[11:]

    return {
        "vin_wmi": wmi,
        "vin_vds": vds,
        "vin_vis": vis,
        "vin_check_digit": normalized[8],
        "vin_year_code": year_code,
        "vin_model_year_estimate": _decode_vin_year_code(year_code),
        "vin_plant_code": plant_code,
        "vin_serial_number": serial_number,
        "vin_country_hint": VIN_COUNTRY_CODES.get(normalized[0], ""),
    }


def decode_vin_with_nhtsa(vin):
    cached = load_vin_cache(get_setting, vin)
    if cached and cached.get("_cache_version") == 4:
        return cached

    payload = fetch_json(NHTSA_DECODE_URL.format(vin=quote(vin)))
    results = payload.get("Results") or []

    if not results:
        raise RuntimeError("NHTSA returned no VIN data.")

    item = results[0]

    model = _join_vehicle_fields(
        item.get("Model"),
        item.get("Series"),
        item.get("Series2"),
        item.get("Trim"),
        item.get("Trim2"),
    )
    fuel_type = _join_vehicle_fields(
        item.get("FuelTypePrimary"),
        item.get("FuelTypeSecondary"),
        item.get("ElectrificationLevel"),
    )
    body_class = _first_vehicle_field(
        item.get("BodyClass"),
        item.get("VehicleType"),
        item.get("BodyCabType"),
    )
    engine_cylinders = _clean_vehicle_field(item.get("EngineCylinders"))
    displacement_l = _clean_vehicle_field(item.get("DisplacementL"))
    engine_hp = _clean_vehicle_field(item.get("EngineHP"))
    engine_summary = _join_vehicle_fields(
        f"{engine_cylinders} cyl" if engine_cylinders else "",
        f"{displacement_l} L" if displacement_l else "",
        item.get("EngineModel"),
        f"{engine_hp} hp" if engine_hp else "",
    )
    drive_type = _first_vehicle_field(
        item.get("DriveType"),
        item.get("TransmissionStyle"),
    )
    plant_location = _join_vehicle_fields(
        item.get("PlantCity"),
        item.get("PlantState"),
        item.get("PlantCountry"),
        separator=", ",
    )

    decoded = {
        "_cache_version": 4,
        "make": _first_vehicle_field(item.get("Make"), item.get("Manufacturer")),
        "model": model,
        "model_year": _clean_vehicle_field(item.get("ModelYear")),
        "manufacturer": _clean_vehicle_field(item.get("Manufacturer")),
        "vehicle_type": _clean_vehicle_field(item.get("VehicleType")),
        "body_class": body_class,
        "series": _join_vehicle_fields(item.get("Series"), item.get("Series2")),
        "trim": _join_vehicle_fields(item.get("Trim"), item.get("Trim2")),
        "fuel_type": fuel_type,
        "fuel_type_secondary": _clean_vehicle_field(item.get("FuelTypeSecondary")),
        "engine_cylinders": engine_cylinders,
        "engine_displacement_l": displacement_l,
        "engine_model": _clean_vehicle_field(item.get("EngineModel")),
        "engine_power_hp": engine_hp,
        "engine_configuration": _clean_vehicle_field(item.get("EngineConfiguration")),
        "engine_summary": engine_summary,
        "drive_type": drive_type,
        "transmission_style": _clean_vehicle_field(item.get("TransmissionStyle")),
        "plant_country": _clean_vehicle_field(item.get("PlantCountry")),
        "plant_state": _clean_vehicle_field(item.get("PlantState")),
        "plant_city": _clean_vehicle_field(item.get("PlantCity")),
        "plant_company": _clean_vehicle_field(item.get("PlantCompanyName")),
        "plant_location": plant_location,
        "doors": _clean_vehicle_field(item.get("Doors")),
        "seats": _clean_vehicle_field(item.get("Seats")),
        "electrification_level": _clean_vehicle_field(item.get("ElectrificationLevel")),
        "error_code": _clean_vehicle_field(item.get("ErrorCode")),
        "error_text": _clean_vehicle_field(item.get("ErrorText")),
    }
    decoded.update(_decode_vin_structure(vin))
    decoded["model_year"] = _first_vehicle_field(decoded.get("model_year"), decoded.get("vin_model_year_estimate"))
    decoded["plant_location"] = _first_vehicle_field(decoded.get("plant_location"), decoded.get("vin_country_hint"))
    decoded["extra_details"] = _build_vin_extra_details(decoded)
    save_vin_cache(set_setting, vin, decoded)
    return decoded


def normalize_plate(plate):
    return re.sub(r"[^A-Za-z0-9]", "", str(plate or "").upper())


def lookup_plate_with_rdw(plate):
    normalized_plate = normalize_plate(plate)

    if not normalized_plate:
        raise RuntimeError("Plate is empty.")

    where_clause = quote(f"kenteken = '{normalized_plate}'")
    url = (
        f"{RDW_DATASET_URL}?$where={where_clause}"
        "&$limit=1"
    )

    rows = fetch_json(url)

    if not rows:
        raise RuntimeError("No RDW vehicle found for this plate.")

    row = rows[0]

    return normalized_plate, {
        "plate": row.get("kenteken", normalized_plate),
        "brand": row.get("merk", ""),
        "model": row.get("handelsbenaming", ""),
        "vehicle_type": row.get("voertuigsoort", ""),
        "first_registration": row.get("datum_eerste_toelating", ""),
        "first_registration_nl": row.get("datum_eerste_tenaamstelling_in_nederland", ""),
        "apk_expiry": row.get("vervaldatum_apk", ""),
        "fuel": _build_rdw_fuel_description(row),
        "color": row.get("eerste_kleur", ""),
        "seats": row.get("aantal_zitplaatsen", ""),
        "doors": row.get("aantal_deuren", ""),
        "weight_empty": row.get("massa_ledig_voertuig", ""),
        "engine_cc": row.get("cilinderinhoud", ""),
        "cylinders": row.get("aantal_cilinders", ""),
        "power_kw": row.get("vermogen_massarijklaar", ""),
        "emission_class": row.get("emissieklasse", ""),
        "catalog_price": row.get("catalogusprijs", ""),
        "body": row.get("inrichting", ""),
        "wheelbase": row.get("wielbasis", ""),
    }


def refresh_vin_profile():

    with app_state.vin_refresh_lock:
        if app_state.vin_refresh_in_progress:
            return False, "VIN refresh already running."
        app_state.vin_refresh_in_progress = True

    if get_demo_mode_enabled():
        update_vehicle_profile(**build_demo_vehicle_profile())
        with app_state.vin_refresh_lock:
            app_state.vin_refresh_in_progress = False
        return True, "Demo VIN loaded."

    update_vehicle_profile(
        vin_status="loading",
        vin_message="Reading VIN from vehicle...",
        vin_last_update=now_time()
    )

    try:
        vin = read_vin()
        update_vehicle_profile(
            vin=vin,
            vin_status="loading",
            vin_message="VIN found. Loading vehicle details...",
            vin_last_update=now_time()
        )

        decoded = decode_vin_with_nhtsa(vin)
        update_vehicle_profile(
            vin=vin,
            decoded=decoded,
            vin_status="ready",
            vin_message="VIN loaded and decoded successfully.",
            vin_last_update=now_time()
        )
        return True, "VIN loaded successfully."
    except (HTTPError, URLError) as e:
        log_error("Decode VIN", e)
        update_vehicle_profile(
            vin_status="error",
            vin_message="VIN read may have worked, but online decode is unavailable right now.",
            vin_last_update=now_time()
        )
        return False, friendly_message(e, source="Decode VIN")
    except Exception as e:
        log_error("Read VIN", e)
        update_vehicle_profile(
            vin_status="error",
            vin_message=friendly_message(e, source="Read VIN"),
            vin_last_update=now_time()
        )
        return False, friendly_message(e, source="Read VIN")
    finally:
        with app_state.vin_refresh_lock:
            app_state.vin_refresh_in_progress = False


def auto_refresh_vin_if_needed():
    with app_state.state_lock:
        vin = app_state.vehicle_profile.get("vin", "")
        vin_status = app_state.vehicle_profile.get("vin_status", "idle")

    if vin or vin_status == "loading":
        return

    refresh_vin_profile()


def set_manual_vin(raw_vin):
    vin = normalize_vin(raw_vin)

    if not vin:
        update_vehicle_profile(
            vin_status="error",
            vin_message="Manual VIN is invalid. Use 17 letters and numbers.",
            vin_last_update=now_time()
        )
        return False, "Enter a valid 17-character VIN."

    update_vehicle_profile(
        vin=vin,
        decoded={},
        vin_status="loading",
        vin_message="Manual VIN saved. Loading vehicle details...",
        vin_last_update=now_time()
    )

    try:
        decoded = decode_vin_with_nhtsa(vin)
        update_vehicle_profile(
            vin=vin,
            decoded=decoded,
            vin_status="ready",
            vin_message="Manual VIN loaded and decoded successfully.",
            vin_last_update=now_time()
        )
        return True, "Manual VIN saved."
    except (HTTPError, URLError) as e:
        log_error("Decode VIN", e)
        update_vehicle_profile(
            vin=vin,
            decoded={},
            vin_status="error",
            vin_message="VIN saved, but online vehicle details are unavailable right now.",
            vin_last_update=now_time()
        )
        return False, friendly_message(e, source="Decode VIN")
    except Exception as e:
        log_error("Decode VIN", e)
        update_vehicle_profile(
            vin=vin,
            decoded={},
            vin_status="error",
            vin_message="VIN saved, but vehicle details could not be loaded.",
            vin_last_update=now_time()
        )
        return False, "VIN saved, but vehicle details could not be loaded."


def refresh_plate_profile(plate):
    if get_demo_mode_enabled():
        update_vehicle_profile(
            plate_query=normalize_plate(plate),
            plate_status="ready",
            plate_message="Demo mode does not perform RDW lookups.",
            plate_last_update=now_time(),
            rdw={}
        )
        return True, "Demo mode RDW placeholder loaded."

    update_vehicle_profile(
        plate_query=normalize_plate(plate),
        plate_status="loading",
        plate_message="Looking up RDW vehicle data...",
        plate_last_update=now_time()
    )

    try:
        normalized_plate, rdw_data = lookup_plate_with_rdw(plate)
        update_vehicle_profile(
            plate_query=normalized_plate,
            rdw=rdw_data,
            plate_status="ready",
            plate_message="RDW vehicle data loaded.",
            plate_last_update=now_time()
        )
        return True, "RDW lookup completed."
    except (HTTPError, URLError) as e:
        log_error("Lookup RDW", e)
        update_vehicle_profile(
            plate_status="error",
            plate_message="RDW could not be reached right now.",
            plate_last_update=now_time()
        )
        return False, friendly_message(e, source="Lookup RDW")
    except Exception as e:
        log_error("Lookup RDW", e)
        update_vehicle_profile(
            plate_status="error",
            plate_message=friendly_message(e, source="Lookup RDW"),
            plate_last_update=now_time()
        )
        return False, friendly_message(e, source="Lookup RDW")


def reset_vehicle_profile():

    with app_state.state_lock:
        app_state.vehicle_profile = {
            "vin": "",
            "vin_status": "idle",
            "vin_message": "VIN not loaded yet.",
            "vin_last_update": None,
            "decoded": {},
            "plate_query": "",
            "plate_status": "idle",
            "plate_message": "No plate lookup yet.",
            "plate_last_update": None,
            "rdw": {}
        }
        app_state.vin_autoload_attempted = False


def update_vehicle_profile(**updates):
    with app_state.state_lock:
        app_state.vehicle_profile.update(updates)
