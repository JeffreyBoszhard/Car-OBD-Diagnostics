#
# ============================================================================
# WARNING - READ BEFORE EDITING
#
# This file is the diagnostic trouble code (DTC) reference database used to
# explain fault codes to the user (description, affected system, severity,
# likely causes). It is loaded by scanner_core/dtc_catalog.py and directly
# affects what every user sees when a code is read from a car, including the
# "Possible serious issue. Inspect before buying." severity warnings shown
# on the Codes page.
#
# DO NOT REMOVE THIS FILE. dtc_catalog.py imports it; removing it or renaming
# its dict variables will break fault code lookups app-wide, and any codes
# missing from here silently fall back to a generic "manufacturer-specific
# code, check documentation" guess (see dtc_catalog.py's _guess_* helpers).
#
# ONLY EDIT THIS FILE IF YOU ARE CONFIDENT IN THE CODE, ITS SEVERITY AND ITS
# CAUSES. A wrong severity or wrong cause list here is actively misleading -
# it can tell a buyer a serious fault is minor, or send someone chasing the
# wrong part. If you are not sure a code/description/severity is correct,
# leave it out entirely; an unlisted code still gets a safe generic fallback
# from dtc_catalog.py, which is better than a wrong specific answer.
#
# Format for every entry, matching scanner_core/dtc_catalog.py's DTC_INFO:
#   "CODE": (description, system, severity, [list of likely causes])
# severity must be one of: "high", "medium", "low"
# ============================================================================
"""Extended DTC reference data: additional generic SAE codes beyond the base
set in dtc_catalog.py, plus a curated (not exhaustive) set of commonly-seen
manufacturer-specific P1xxx codes per brand.

This is deliberately NOT a complete database of every code every
manufacturer has ever defined - each carmaker maintains thousands of
proprietary codes in dealer-only documentation (Toyota TIS, Ford IDS, GM
ACDelco, etc.) that are not fully public. What's here are the codes that are
well-documented and commonly encountered in the field. Add to it over time;
do not treat an empty lookup as "this code doesn't exist" - it just means
nobody has added it here yet, and the generic fallback in dtc_catalog.py
will still show something reasonable.
"""

# ----------------------------------------------------------------------
# Additional generic (SAE-standard, brand-independent) codes not already
# covered by DTC_INFO in dtc_catalog.py.
# ----------------------------------------------------------------------
EXTRA_GENERIC_CODES = {
    "P0501": ("Vehicle speed sensor range/performance", "Speed", "medium", ["VSS sensor", "wiring", "tone ring/ABS ring"]),
    "P0562": ("System voltage too low", "Electrical", "medium", ["weak battery", "alternator", "ground/cables"]),
    "P0563": ("System voltage too high", "Electrical", "medium", ["alternator regulator", "battery", "wiring"]),
    "P0605": ("ECU ROM checksum fault", "ECU", "high", ["internal ECU fault", "software", "power supply"]),
    "P0620": ("Alternator/generator control circuit", "Charging", "medium", ["alternator", "regulator", "wiring"]),
    "P0630": ("VIN not programmed to ECU", "ECU", "low", ["ECU replacement not programmed", "dealer programming needed"]),
    "P0638": ("Throttle actuator control range/performance", "Throttle", "high", ["throttle body", "throttle actuator", "wiring"]),
    "P0641": ("Sensor reference voltage A circuit", "Electrical", "medium", ["sensor reference circuit", "wiring", "short/open"]),
    "P0650": ("Malfunction indicator lamp control circuit", "ECU", "low", ["MIL bulb/wiring", "ECU driver circuit"]),
    "P0685": ("ECU main relay control circuit", "Electrical", "high", ["main relay", "wiring", "possible no-start"]),
    "P0700": ("Transmission control system malfunction", "Transmission", "medium", ["TCM has stored codes", "scan transmission module with enhanced scanner"]),
    "P0705": ("Transmission range sensor circuit", "Transmission", "medium", ["neutral safety/range sensor", "wiring", "linkage"]),
    "P0706": ("Transmission range sensor range/performance", "Transmission", "medium", ["range sensor", "linkage adjustment"]),
    "P0710": ("Transmission fluid temperature sensor circuit", "Transmission", "medium", ["fluid temp sensor", "wiring", "low fluid"]),
    "P0715": ("Input/turbine speed sensor circuit", "Transmission", "medium", ["input speed sensor", "wiring", "internal transmission wear"]),
    "P0720": ("Output speed sensor circuit", "Transmission", "medium", ["output speed sensor", "wiring", "tone ring"]),
    "P0725": ("Engine speed input circuit", "Transmission", "medium", ["crank signal to TCM", "wiring"]),
    "P0730": ("Incorrect gear ratio", "Transmission", "high", ["internal transmission wear", "solenoid", "low fluid", "possible rebuild needed"]),
    "P0740": ("Torque converter clutch circuit", "Transmission", "medium", ["TCC solenoid", "wiring", "fluid condition"]),
    "P0741": ("Torque converter clutch stuck off", "Transmission", "medium", ["TCC solenoid", "low fluid", "converter wear"]),
    "P0750": ("Shift solenoid A malfunction", "Transmission", "medium", ["shift solenoid", "wiring", "valve body"]),
    "P0755": ("Shift solenoid B malfunction", "Transmission", "medium", ["shift solenoid", "wiring", "valve body"]),
    "P0760": ("Shift solenoid C malfunction", "Transmission", "medium", ["shift solenoid", "wiring", "valve body"]),
    "P0770": ("Shift solenoid D malfunction", "Transmission", "medium", ["shift solenoid", "wiring", "valve body"]),
    "P0780": ("Shift malfunction", "Transmission", "medium", ["valve body", "solenoids", "low or dirty fluid"]),
    "P0793": ("Intermediate shaft speed sensor circuit", "Transmission", "medium", ["intermediate shaft sensor", "wiring"]),
    "P0801": ("Reverse inhibit control circuit", "Transmission", "low", ["reverse lockout circuit", "wiring"]),
    "P0850": ("Park/neutral switch input circuit", "Transmission", "low", ["park/neutral switch", "wiring", "possible no-start"]),
    "P0A80": ("Replace hybrid battery pack", "Hybrid/EV", "high", ["hybrid battery pack degraded", "cell imbalance", "likely expensive repair"]),
    "P0AA6": ("Hybrid/EV battery voltage system isolation fault", "Hybrid/EV", "high", ["high-voltage isolation fault", "requires qualified hybrid/EV technician"]),
    "P0C68": ("High voltage battery temperature sensor circuit", "Hybrid/EV", "medium", ["battery temp sensor", "cooling system", "wiring"]),
    "P2002": ("Diesel particulate filter efficiency below threshold bank 1", "DPF/Diesel", "medium", ["clogged DPF", "needs regeneration", "sensor"]),
    "P2096": ("Post-catalyst fuel trim system too lean bank 1", "Fuel mixture", "medium", ["exhaust leak after cat", "fuel pressure", "O2 sensor"]),
    "P2097": ("Post-catalyst fuel trim system too rich bank 1", "Fuel mixture", "medium", ["injector", "fuel pressure regulator", "O2 sensor"]),
    "P2100": ("Throttle actuator control motor circuit", "Throttle", "high", ["electronic throttle body", "wiring", "ECU driver"]),
    "P2101": ("Throttle actuator control motor range/performance", "Throttle", "high", ["throttle body", "carbon buildup", "calibration"]),
    "P2118": ("Throttle actuator control motor current range/performance", "Throttle", "high", ["throttle actuator motor", "wiring"]),
    "P2122": ("Accelerator pedal position sensor D circuit low", "Throttle", "high", ["pedal position sensor", "wiring"]),
    "P2123": ("Accelerator pedal position sensor D circuit high", "Throttle", "high", ["pedal position sensor", "wiring"]),
    "P2126": ("Accelerator pedal position sensor E circuit low", "Throttle", "high", ["pedal position sensor", "wiring"]),
    "P2135": ("Throttle position sensor A/B correlation", "Throttle", "high", ["TPS sensor pair mismatch", "throttle body", "wiring"]),
    "P2138": ("Accelerator pedal sensor D/E correlation", "Throttle", "high", ["pedal sensor pair mismatch", "wiring"]),
    "P2178": ("System too rich at idle bank 1", "Fuel mixture", "medium", ["injector leak", "fuel pressure", "MAF sensor"]),
    "P2187": ("System too lean at idle bank 1", "Fuel mixture", "medium", ["vacuum leak", "injector", "MAF sensor"]),
    "P2195": ("O2 sensor signal stuck lean bank 1 sensor 1", "O2 sensor", "medium", ["oxygen sensor", "exhaust leak", "fuel delivery"]),
    "P2196": ("O2 sensor signal stuck rich bank 1 sensor 1", "O2 sensor", "medium", ["oxygen sensor", "fuel pressure", "injector"]),
    "P2231": ("O2 sensor signal circuit shorted to heater circuit", "O2 sensor", "medium", ["oxygen sensor wiring", "connector damage"]),
    "P2237": ("O2 sensor positive current control circuit open", "O2 sensor", "medium", ["oxygen sensor", "wiring"]),
    "P2270": ("O2 sensor signal stuck lean bank 1 sensor 2", "O2 sensor", "medium", ["rear oxygen sensor", "exhaust leak"]),
    "P2271": ("O2 sensor signal stuck rich bank 1 sensor 2", "O2 sensor", "medium", ["rear oxygen sensor", "fuel delivery"]),
    "P2279": ("Intake air system leak", "Air metering", "medium", ["intake hose crack", "boot clamp", "vacuum leak"]),
    "P2299": ("Brake pedal/accelerator pedal position incompatible", "Throttle", "medium", ["brake switch", "pedal sensor", "wiring"]),
    "P2302": ("Ignition coil B primary/secondary circuit", "Ignition", "high", ["coil B", "wiring", "spark plug"]),
    "P2303": ("Ignition coil B secondary circuit", "Ignition", "high", ["coil B secondary winding", "spark plug", "wiring"]),
    "P2304": ("Ignition coil C primary/secondary circuit", "Ignition", "high", ["coil C", "wiring", "spark plug"]),
    "P2400": ("EVAP leak detection pump circuit", "EVAP", "low", ["EVAP pump", "wiring", "fuse"]),
    "P2401": ("EVAP leak detection pump control circuit low", "EVAP", "low", ["EVAP pump", "wiring"]),
    "P2402": ("EVAP leak detection pump control circuit high", "EVAP", "low", ["EVAP pump", "wiring"]),
    "P2413": ("EGR system performance", "Emission/EGR", "medium", ["EGR valve", "carbon buildup", "sensor"]),
    "P2419": ("EVAP switching valve control circuit", "EVAP", "low", ["EVAP switching valve", "wiring"]),
    "P2610": ("ECU internal engine off timer performance", "ECU", "low", ["internal ECU fault"]),
    "P2626": ("O2 sensor pumping current trim circuit open bank 1 sensor 1", "O2 sensor", "medium", ["wideband oxygen sensor", "wiring"]),
    "P3400": ("Cylinder deactivation system bank 1", "Cylinder deactivation", "medium", ["lifter solenoid", "oil pressure", "wiring"]),
    "P3500": ("Hybrid battery pack deterioration", "Hybrid/EV", "high", ["hybrid battery cell degradation", "likely expensive repair"]),
    "C0035": ("Left front wheel speed sensor circuit", "ABS/Chassis", "medium", ["wheel speed sensor", "tone ring", "wiring"]),
    "C0040": ("Right front wheel speed sensor circuit", "ABS/Chassis", "medium", ["wheel speed sensor", "tone ring", "wiring"]),
    "C0045": ("Left rear wheel speed sensor circuit", "ABS/Chassis", "medium", ["wheel speed sensor", "tone ring", "wiring"]),
    "C0050": ("Right rear wheel speed sensor circuit", "ABS/Chassis", "medium", ["wheel speed sensor", "tone ring", "wiring"]),
    "C0110": ("ABS pump motor circuit", "ABS", "high", ["ABS pump motor", "relay", "wiring"]),
    "C0161": ("ABS/traction control brake switch circuit", "ABS", "medium", ["brake switch", "wiring"]),
    "U0100": ("Lost communication with ECM/PCM", "Network", "high", ["CAN bus wiring", "ECM power/ground", "module fault"]),
    "U0101": ("Lost communication with TCM", "Network", "high", ["CAN bus wiring", "TCM power/ground"]),
    "U0121": ("Lost communication with ABS module", "Network", "high", ["CAN bus wiring", "ABS module power/ground"]),
    "U0140": ("Lost communication with body control module", "Network", "medium", ["CAN bus wiring", "BCM power/ground"]),
    "U0155": ("Lost communication with instrument panel cluster", "Network", "medium", ["CAN bus wiring", "cluster power/ground"]),
    "U0401": ("Invalid data received from ECM/PCM", "Network", "medium", ["CAN bus interference", "module software mismatch"]),
    "U0402": ("Invalid data received from TCM", "Network", "medium", ["CAN bus interference", "module software mismatch"]),
}


# ----------------------------------------------------------------------
# Manufacturer-specific (P1xxx and similar) codes, keyed by brand name in
# uppercase. Curated common codes only - not exhaustive. Add new brands as
# new top-level dict keys; add new codes as entries inside a brand's dict,
# using the exact same tuple format as EXTRA_GENERIC_CODES above.
# ----------------------------------------------------------------------
MANUFACTURER_CODES = {
    "TOYOTA": {
        "P1100": ("Barometric pressure sensor circuit", "Air pressure", "medium", ["BARO sensor", "wiring", "connector"]),
        "P1120": ("Accelerator pedal position sensor circuit malfunction", "Throttle", "high", ["pedal position sensor", "wiring"]),
        "P1130": ("Air/fuel sensor circuit range/performance bank 1 sensor 1", "O2 sensor", "medium", ["air/fuel ratio sensor", "wiring", "exhaust leak"]),
        "P1135": ("Air/fuel sensor heater circuit bank 1 sensor 1", "O2 sensor", "medium", ["A/F sensor heater", "fuse", "wiring"]),
        "P1150": ("Air/fuel sensor circuit range/performance bank 2 sensor 1", "O2 sensor", "medium", ["air/fuel ratio sensor", "wiring"]),
        "P1155": ("Air/fuel sensor heater circuit bank 2 sensor 1", "O2 sensor", "medium", ["A/F sensor heater", "fuse", "wiring"]),
        "P1300": ("Igniter circuit malfunction", "Ignition", "high", ["igniter", "ignition coil", "wiring"]),
        "P1349": ("VVT system malfunction bank 1", "Timing/VVT", "medium", ["VVT solenoid", "oil pressure", "timing"]),
        "P1400": ("Sub-throttle position sensor circuit", "Throttle", "medium", ["sub-throttle sensor", "wiring"]),
        "P1600": ("ECU battery backup circuit", "ECU", "low", ["battery backup power", "fuse"]),
        "P1656": ("EVAP VSV control circuit", "EVAP", "low", ["EVAP VSV", "wiring"]),
    },
    "FORD": {
        "P1000": ("OBD-II monitor testing not complete", "ECU", "low", ["drive cycle not completed", "recently cleared codes"]),
        "P1130": ("Lack of HO2S switch, adaptive fuel too lean bank 1", "Fuel mixture", "medium", ["fuel pressure", "injector", "O2 sensor"]),
        "P1131": ("Lack of HO2S switch, sensor indicates lean bank 1", "O2 sensor", "medium", ["oxygen sensor", "exhaust leak", "vacuum leak"]),
        "P1220": ("Series throttle control fault", "Throttle", "high", ["throttle body", "APP sensor", "wiring"]),
        "P1246": ("Turbo/supercharger boost sensor A circuit", "Boost", "medium", ["boost pressure sensor", "wiring", "hose"]),
        "P1450": ("Unable to bleed up fuel tank vacuum", "EVAP", "low", ["EVAP canister", "purge valve", "fuel cap"]),
        "P1500": ("Vehicle speed sensor intermittent", "Speed", "medium", ["VSS", "wiring", "connector"]),
        "P1518": ("Intake manifold runner control stuck closed", "Air metering", "medium", ["IMRC actuator", "wiring", "carbon buildup"]),
        "P1608": ("ECU internal fault", "ECU", "high", ["internal ECU fault", "power/ground"]),
        "P1650": ("Power steering pressure switch circuit", "Steering", "low", ["PSP switch", "wiring"]),
    },
    "GM": {
        "P1101": ("MAF sensor out of range", "Air metering", "medium", ["MAF sensor", "intake leak", "wiring"]),
        "P1133": ("HO2S insufficient switching bank 1 sensor 1", "O2 sensor", "medium", ["oxygen sensor", "exhaust leak", "fuel trim"]),
        "P1258": ("Engine coolant over-temperature - protection mode active", "Cooling", "high", ["cooling system", "low coolant", "water pump"]),
        "P1345": ("Crankshaft/camshaft correlation, bank 1 sensor A", "Timing", "high", ["timing chain stretch", "crank/cam sensor"]),
        "P1441": ("EVAP system flow during non-purge", "EVAP", "low", ["purge valve", "EVAP canister"]),
        "P1621": ("PCM memory performance", "ECU", "medium", ["internal ECU fault", "battery/power"]),
        "P1810": ("Transmission fluid pressure manual valve position switch", "Transmission", "medium", ["pressure switch", "valve body"]),
        "P1811": ("Maximum adapt and long shift", "Transmission", "medium", ["worn clutches", "low or dirty fluid", "solenoid"]),
    },
    "HONDA": {
        "P1121": ("Throttle position sensor lower than expected", "Throttle", "medium", ["TPS sensor", "throttle body", "wiring"]),
        "P1259": ("VTEC system malfunction", "Timing/VVT", "medium", ["VTEC solenoid", "oil pressure", "oil level"]),
        "P1298": ("Electrical load detector circuit high input", "Electrical", "low", ["ELD sensor", "wiring"]),
        "P1361": ("TDC sensor A/no signal", "Ignition", "high", ["TDC/crank sensor", "wiring", "timing belt"]),
        "P1456": ("EVAP system leak small (fuel tank system)", "EVAP", "low", ["fuel cap", "EVAP hose", "small leak"]),
        "P1457": ("EVAP system leak (control canister system)", "EVAP", "low", ["EVAP canister", "purge valve"]),
        "P1508": ("Idle air control system low", "Idle", "medium", ["IAC valve", "throttle body", "vacuum leak"]),
        "P1509": ("Idle air control system high", "Idle", "medium", ["IAC valve", "throttle body"]),
    },
    "VOLKSWAGEN": {
        "P1128": ("Fuel trim, bank 1 system too lean", "Fuel mixture", "medium", ["vacuum leak", "fuel pump", "MAF sensor"]),
        "P1136": ("Fuel trim, bank 1 additive too lean", "Fuel mixture", "medium", ["vacuum leak", "injector", "fuel pressure"]),
        "P1176": ("Front HO2S bank 1 internal resistance too high", "O2 sensor", "medium", ["oxygen sensor", "wiring", "connector"]),
        "P1296": ("Cooling system malfunction", "Cooling", "high", ["thermostat", "coolant sensor", "cooling fan"]),
        "P1336": ("Engine torque adaptation at limit", "ECU", "medium", ["throttle body adaptation", "TPS", "internal ECU learn"]),
        "P1519": ("Intake manifold changeover valve stuck", "Air metering", "medium", ["intake flap actuator", "wiring", "vacuum"]),
        "P1602": ("Power supply (battery) voltage too low", "Electrical", "medium", ["battery", "alternator", "ground"]),
    },
    "BMW": {
        "P1055": ("Camshaft position actuator bank 1 intake", "Timing/VVT", "medium", ["VANOS solenoid", "oil pressure", "wiring"]),
        "P1057": ("Camshaft position actuator bank 1 exhaust", "Timing/VVT", "medium", ["VANOS solenoid", "oil pressure"]),
        "P1093": ("Fuel trim too lean bank 1", "Fuel mixture", "medium", ["vacuum leak", "fuel pump", "injector"]),
        "P1288": ("Engine oil temperature sensor implausible signal", "Temperature", "low", ["oil temp sensor", "wiring"]),
        "P1519": ("Air control valve stuck closed", "Air metering", "medium", ["air control valve", "wiring", "vacuum"]),
        "P1624": ("Fault in communication with immobilizer control unit", "ECU", "medium", ["immobilizer", "key/transponder", "wiring"]),
    },
    "NISSAN": {
        "P1148": ("Closed loop control bank 1", "Fuel mixture", "medium", ["oxygen sensor", "fuel trim adaptation", "vacuum leak"]),
        "P1211": ("ABS/traction control unit signal circuit malfunction", "ABS/Chassis", "medium", ["ABS wiring", "ABS module communication"]),
        "P1320": ("Ignition signal missing", "Ignition", "high", ["ignition coil", "crank sensor", "wiring"]),
        "P1400": ("EGR/EVAP canister purge control solenoid valve", "Emission", "low", ["purge solenoid", "wiring"]),
        "P1550": ("Power steering pressure sensor circuit", "Steering", "low", ["power steering pressure sensor", "wiring"]),
    },
    "HYUNDAI_KIA": {
        "P1326": ("Knock sensor 1 circuit intermittent", "Ignition", "medium", ["knock sensor", "wiring", "engine knock"]),
        "P1529": ("Cooling fan relay 2 control circuit", "Cooling", "medium", ["cooling fan relay", "wiring", "fuse"]),
        "P1602": ("ECU battery backup circuit malfunction", "ECU", "low", ["ECU backup power", "fuse"]),
        "P1655": ("EVAP purge control solenoid valve control circuit", "EVAP", "low", ["purge solenoid", "wiring"]),
        "P1740": ("Torque converter clutch system malfunction", "Transmission", "medium", ["TCC solenoid", "fluid condition"]),
    },
    "CHRYSLER": {
        "P1281": ("Engine coolant temperature below thermostat regulating temperature", "Cooling", "low", ["thermostat stuck open", "ECT sensor"]),
        "P1288": ("Manifold air/fuel temperature sensor voltage high", "Temperature", "low", ["intake temp sensor", "wiring"]),
        "P1391": ("Intermittent loss of camshaft or crankshaft position sensor", "Timing", "high", ["crank/cam sensor", "wiring", "reluctor"]),
        "P1494": ("EVAP leak monitor circuit", "EVAP", "low", ["EVAP pressure sensor", "wiring"]),
        "P1698": ("No CCD/SCI or J1850 message received from TCM", "Network", "medium", ["CAN/SCI bus wiring", "TCM communication"]),
    },
    "MAZDA": {
        "P1128": ("Long term fuel trim malfunction bank 1", "Fuel mixture", "medium", ["vacuum leak", "MAF sensor", "fuel pressure"]),
        "P1259": ("Variable tumble control system malfunction", "Air metering", "medium", ["VTCS actuator", "wiring", "vacuum"]),
        "P1345": ("Camshaft position sensor circuit range/performance", "Timing", "high", ["camshaft sensor", "wiring", "timing"]),
        "P1602": ("PCM battery backup malfunction", "ECU", "low", ["ECU backup power", "fuse"]),
    },
    "SUBARU": {
        "P1400": ("Fuel tank pressure control system malfunction", "EVAP", "low", ["EVAP control valve", "wiring"]),
        "P1443": ("EVAP purge flow monitor malfunction", "EVAP", "low", ["purge valve", "EVAP hoses"]),
        "P1507": ("Idle control system malfunction (RPM higher than expected)", "Idle", "medium", ["idle control valve", "throttle body", "vacuum leak"]),
        "P1518": ("Crankshaft/camshaft position sensor correlation", "Timing", "high", ["crank/cam sensor", "timing belt/chain"]),
    },
}


def get_manufacturer_codes(brand):
    """Look up the curated manufacturer-code dict for a brand name (any
    case, e.g. from a saved vehicle profile's Brand field). Returns an
    empty dict for unknown/unlisted brands - callers should treat that the
    same as "no manufacturer-specific data available", not as an error."""
    if not brand:
        return {}

    key = str(brand).strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "CHEVROLET": "GM",
        "CHEVY": "GM",
        "GMC": "GM",
        "BUICK": "GM",
        "CADILLAC": "GM",
        "AUDI": "VOLKSWAGEN",
        "VW": "VOLKSWAGEN",
        "SKODA": "VOLKSWAGEN",
        "SEAT": "VOLKSWAGEN",
        "DODGE": "CHRYSLER",
        "JEEP": "CHRYSLER",
        "RAM": "CHRYSLER",
        "HYUNDAI": "HYUNDAI_KIA",
        "KIA": "HYUNDAI_KIA",
        "LEXUS": "TOYOTA",
        "SCION": "TOYOTA",
        "ACURA": "HONDA",
        "INFINITI": "NISSAN",
        "MINI": "BMW",
    }
    key = aliases.get(key, key)
    return MANUFACTURER_CODES.get(key, {})
