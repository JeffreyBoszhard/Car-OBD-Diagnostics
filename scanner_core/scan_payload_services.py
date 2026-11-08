"""Building, exporting and persisting the full scan payload: the health
report, battery check, connection quality and demo/report sections combined
into one snapshot, plus saved-scan and garage-note storage and CSV/HTML
export rendering.

current_scan_payload() is on the hot path - it backs the live-data stream -
so it is careful about what it does per call. The expensive parts (supported
PIDs, Mode 06, the JSON state files, the settings table, serial port
enumeration) are all cached by the modules that own them, and the fuel-trim
history is sampled on a timer instead of being appended, and the whole
44 KB file rewritten, on every single frame.
"""
import csv
import io
import time
from html import escape

from config import APP_VERSION, FUEL_TRIM_HISTORY_LIMIT, FUEL_TRIM_SAMPLE_INTERVAL, SCAN_HISTORY_LIMIT
from scanner_core import app_state
from scanner_core.attachment_services import prune_orphan_attachments
from scanner_core.demo_services import get_demo_preset, get_demo_presets, normalize_demo_preset
from scanner_core.extended_pid_services import get_extended_data
from scanner_core.garage_services import normalize_garage_plate, normalize_garage_vin
from scanner_core.health_services import (
    build_battery_check,
    build_health_report,
    build_mode06_snapshot,
    build_pid_support_summary,
    build_simple_summary,
    enrich_connection_quality,
)
from scanner_core.obd_services import connection_quality_snapshot as build_connection_quality_snapshot
from scanner_core.report_services import build_purchase_report
from scanner_core.session_services import build_scanner_session_state
from scanner_core.settings_services import get_demo_preset_name
from scanner_core.setup_services import (
    get_dashboard_setup,
    get_runtime_state,
    infer_obd_bus_mode,
    save_json_file,
)
from scanner_core.storage_services import (
    delete_garage_note as storage_delete_garage_note,
    delete_scan_snapshot as storage_delete_scan_snapshot,
    get_all_attachment_names as storage_get_all_attachment_names,
    get_garage_note as storage_get_garage_note,
    get_recent_garage_notes as storage_get_recent_garage_notes,
    get_recent_scans as storage_get_recent_scans,
    save_garage_note as storage_save_garage_note,
    save_scan_snapshot as storage_save_scan_snapshot,
    update_garage_note as storage_update_garage_note,
)
from scanner_core.trip_services import get_current_fuel_rate, get_trip_summary
from scanner_core.units_services import apply_unit_conversions, number_from_value
from scanner_core.vin_services import normalize_vin

_last_fuel_trim_sample = 0.0


def append_fuel_trim_history(payload, force=False):
    """Append one fuel-trim sample, at most once per sample interval.

    This used to run on every /api/data request - ten times a second - and
    each run rewrote the whole dashboard_runtime.json file. A fuel trim
    reading does not change meaningfully in 100 ms, so one sample every
    FUEL_TRIM_SAMPLE_INTERVAL seconds carries the same information for a
    fraction of the disk writes.
    """
    global _last_fuel_trim_sample

    now = time.monotonic()
    if not force and (now - _last_fuel_trim_sample) < FUEL_TRIM_SAMPLE_INTERVAL:
        return False

    vehicle = payload.get("vehicle", {})
    stft = number_from_value(vehicle.get("short_fuel_trim_1", {}).get("value"))
    ltft = number_from_value(vehicle.get("long_fuel_trim_1", {}).get("value"))
    if stft is None and ltft is None:
        return False

    _last_fuel_trim_sample = now
    state = get_runtime_state()
    history = state.get("fuel_trim_history") if isinstance(state.get("fuel_trim_history"), list) else []
    history.append({"time": payload.get("created_at"), "stft": stft, "ltft": ltft})
    state["fuel_trim_history"] = history[-FUEL_TRIM_HISTORY_LIMIT:]
    save_json_file(app_state.DASHBOARD_RUNTIME_PATH, state)
    return True


EXPORT_PRESETS = {
    "full": None,
    "codes": {"dtc", "freeze_frame", "readiness"},
    "live": {"vehicle", "pid_support"},
    "vehicle": {"vehicle_profile"},
}


def current_scan_payload(units="metric"):
    with app_state.state_lock:
        status = dict(app_state.obd_status)
        vehicle = dict(app_state.vehicle_data)
        dtc = {
            "stored": list(app_state.dtc_data["stored"]),
            "pending": list(app_state.dtc_data["pending"]),
            "permanent": list(app_state.dtc_data["permanent"])
        }
        profile = dict(app_state.vehicle_profile)
        readiness = dict(app_state.readiness_data)
        freeze_frame = dict(app_state.freeze_frame_data)
        demo_preset = normalize_demo_preset(app_state.demo_drive_state.get("preset", get_demo_preset_name()))

    raw_speed_kmh = number_from_value(vehicle.get("speed", {}).get("value"))
    raw_maf_g_s = number_from_value(vehicle.get("maf", {}).get("value"))

    connection_quality = (
        {
            "phase": "Demo mode",
            "adapter_connected": True,
            "port_powered": True,
            "car_connected": True,
            "live_data_active": True,
        }
        if status.get("demo_mode")
        else build_connection_quality_snapshot(app_state.connection, status.get("connecting"), status.get("error"))
    )
    connection_quality = enrich_connection_quality(status, connection_quality)
    session_state = build_scanner_session_state(
        status,
        connection_quality,
        status.get("connection_hint"),
    )
    preset_id, preset_meta = get_demo_preset(demo_preset)

    payload = {
        "app_version": APP_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "session_state": {
            **session_state,
            "demo_preset": preset_id,
            "demo_preset_label": preset_meta["label"],
        },
        "vehicle": apply_unit_conversions(vehicle, units),
        "dtc": dtc,
        "vehicle_profile": profile,
        "connection_quality": connection_quality,
        "connection_hint": dict(status.get("connection_hint") or {}),
        "readiness": readiness,
        "freeze_frame": freeze_frame,
        "health": build_health_report(units),
        "battery_check": build_battery_check(),
        "pid_support": build_pid_support_summary(),
        "extended": get_extended_data(),
        "standard_obd_only": True,
        "obd_bus_mode": infer_obd_bus_mode(status.get("protocol")),
        "dashboard_setup": get_dashboard_setup(),
        "runtime_state": get_runtime_state(),
        "mode06": build_mode06_snapshot(),
        "demo": {
            "enabled": bool(status.get("demo_mode")),
            "preset": preset_id,
            "presets": get_demo_presets(),
        },
        "trip": {
            **get_trip_summary(units),
            "live_consumption": get_current_fuel_rate(raw_speed_kmh, raw_maf_g_s, units),
        },
    }
    payload["report"] = build_purchase_report(payload)
    payload["simple_summary"] = build_simple_summary(payload)
    return payload


def save_scan_snapshot(label, units="metric", vehicle_id=None):
    payload = current_scan_payload(units)
    created_at = payload["created_at"]
    health = payload["health"]
    status = payload["status"]
    summary = (
        f"{health['status'].upper()} | score {health['score']} | stored {health['counts']['stored']} "
        f"| pending {health['counts']['pending']} | {status.get('protocol', 'Unknown')} | "
        f"{status.get('current_port') or 'auto'}"
    )
    return storage_save_scan_snapshot(app_state.DB_PATH, created_at, label, summary, payload, vehicle_id)


def get_recent_garage_notes(limit=SCAN_HISTORY_LIMIT, vehicle_id=None, with_payload=False):
    """Newest-first garage notes.

    with_payload defaults to False: the list views, the search filter, the
    export and the maintenance estimate all only need the note itself, and
    the stored scan snapshot attached to each note is by far the biggest
    thing in the table.
    """
    return storage_get_recent_garage_notes(app_state.DB_PATH, limit, vehicle_id, with_payload)


def get_garage_note(note_id):
    return storage_get_garage_note(app_state.DB_PATH, int(note_id))


def save_garage_note_snapshot(vin, plate, title, mileage, note, attachment_name=None,
                              units="metric", vehicle_id=None):
    payload = current_scan_payload(units)
    created_at = payload["created_at"]
    vin = normalize_vin(vin)
    plate = normalize_garage_plate(plate)
    return storage_save_garage_note(
        app_state.DB_PATH,
        created_at,
        vin,
        plate,
        title.strip() or "Garage note",
        mileage.strip() or "--",
        note.strip(),
        payload,
        vehicle_id,
        attachment_name,
    )


def delete_garage_note(note_id):
    deleted = storage_delete_garage_note(app_state.DB_PATH, int(note_id))
    if deleted:
        # The photo that belonged to this note is now unreferenced.
        prune_orphan_attachments(storage_get_all_attachment_names(app_state.DB_PATH))
    return deleted


def update_garage_note(note_id, vin, plate, title, mileage, note):
    return storage_update_garage_note(
        app_state.DB_PATH,
        int(note_id),
        normalize_garage_vin(vin),
        normalize_garage_plate(plate),
        title.strip() or "Garage note",
        mileage.strip() or "--",
        note.strip(),
    )


def apply_export_preset(payload, preset):
    """Return a copy of the scan payload trimmed to the requested export preset.

    'full' keeps everything. Every other preset keeps the shared header blocks
    (status/health/meta) and blanks the sections that preset does not cover, so
    the exported report actually matches what the user picked in the dropdown.
    """
    keep = EXPORT_PRESETS.get(str(preset or "full").lower(), None)
    if not keep:
        return payload

    trimmed = dict(payload)
    optional = {
        "dtc": {"stored": [], "pending": [], "permanent": []},
        "freeze_frame": {"available": False, "values": {}},
        "readiness": {"available": False, "monitors": []},
        "vehicle": {},
        "pid_support": {},
        "vehicle_profile": {},
    }
    for section, blank in optional.items():
        if section not in keep:
            trimmed[section] = blank
    trimmed["export_preset"] = str(preset).lower()
    return trimmed


def get_recent_scans(limit=SCAN_HISTORY_LIMIT):
    return storage_get_recent_scans(app_state.DB_PATH, limit)


def delete_scan_snapshot(scan_id):
    return storage_delete_scan_snapshot(app_state.DB_PATH, int(scan_id))


def render_scan_csv(payload):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "key", "value"])
    writer.writerow(["meta", "created_at", payload.get("created_at", "")])
    writer.writerow(["status", "protocol", payload.get("status", {}).get("protocol", "")])
    for key, item in (payload.get("vehicle") or {}).items():
        writer.writerow(["live", item.get("label", key), item.get("value", "")])
    for group, codes in (payload.get("dtc") or {}).items():
        for code in codes:
            writer.writerow([f"dtc_{group}", code.get("code", ""), code.get("description_en") or code.get("description") or ""])
    for item in (payload.get("readiness") or {}).get("monitors", []):
        writer.writerow(["readiness", item.get("name", ""), "ready" if item.get("complete") else "incomplete"])
    extended = payload.get("extended") or {}
    odometer = extended.get("odometer") or {}
    if odometer.get("km") is not None:
        writer.writerow(["extended", "odometer_km", odometer.get("km")])
    return output.getvalue()


def render_export_html(payload):
    status = payload.get("status", {})
    vehicle = payload.get("vehicle", {})
    dtc = payload.get("dtc", {})
    health = payload.get("health", {})
    battery = payload.get("battery_check", {})
    readiness = payload.get("readiness", {})
    report = payload.get("report", {})
    extended = payload.get("extended", {}) or {}
    odometer = extended.get("odometer") or {}

    def row(label, value):
        safe_value = value if value not in (None, "") else "--"
        return f"<tr><th>{escape(str(label))}</th><td>{escape(str(safe_value))}</td></tr>"

    def code_rows(title, codes):
        if not codes:
            return f"<h2>{escape(title)}</h2><p class=\"ok\">No codes.</p>"
        rows = []
        for item in codes:
            causes = ", ".join(item.get("possible_causes") or [])
            severity = str(item.get("severity") or "--")
            rows.append(
                "<tr>"
                f"<td class=\"code\">{escape(item.get('code', '--'))}</td>"
                f"<td>{escape(item.get('description_en') or item.get('description') or '--')}</td>"
                f"<td>{escape(item.get('system') or '--')}</td>"
                f"<td><span class=\"sev sev-{escape(severity.lower().replace(' ', '-'))}\">{escape(severity)}</span></td>"
                f"<td>{escape(causes or '--')}</td>"
                "</tr>"
            )
        return (
            f"<h2>{escape(title)}</h2>"
            "<table><thead><tr><th>Code</th><th>Description</th><th>System</th><th>Severity</th><th>Possible causes</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    readiness_items = readiness.get("monitors", []) if readiness.get("available") else []
    readiness_rows = "".join(
        row(item.get("name", "Monitor"), "Ready" if item.get("complete") else "Incomplete")
        for item in readiness_items
    ) or row("Readiness", "No readiness data available")

    report_sections = "".join(
        f"<h3>{escape(section.get('title', 'Section'))}</h3><ul>"
        + "".join(f"<li>{escape(str(item))}</li>" for item in section.get("items", []))
        + "</ul>"
        for section in report.get("sections", [])
    )

    score = health.get("score", "--")
    score_status = str(health.get("status") or "unknown")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OBD Scan Report {escape(payload.get('created_at', ''))}</title>
  <style>
    :root {{
      --ink: #0d1623; --muted: #5d6f87; --line: #dde5ef;
      --accent: #0f8cff; --good: #0ca678; --warn: #f59f00; --bad: #e5484d;
      --panel: #ffffff; --canvas: #f2f6fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      background: var(--canvas); color: var(--ink); margin: 0;
      font: 14px/1.6 "Segoe UI", Inter, Arial, sans-serif;
      -webkit-print-color-adjust: exact; print-color-adjust: exact;
    }}
    .sheet {{ max-width: 1040px; margin: 0 auto; padding: 32px 28px 64px; }}
    header.hero {{
      background: linear-gradient(135deg, #0b1b30 0%, #123a63 55%, #0f8cff 140%);
      border-radius: 20px; color: #fff; padding: 28px 30px; position: relative; overflow: hidden;
    }}
    header.hero::after {{
      content: ""; position: absolute; inset: auto -40px -70px auto; width: 240px; height: 240px;
      background: radial-gradient(circle, rgba(255,255,255,.18), transparent 70%); border-radius: 50%;
    }}
    header.hero .eyebrow {{ font-size: 11px; letter-spacing: .18em; text-transform: uppercase; opacity: .78; margin: 0; }}
    header.hero h1 {{ font-size: 28px; margin: 6px 0 4px; }}
    header.hero p {{ margin: 0; opacity: .85; }}
    .scorecard {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px; margin: -26px 6px 22px; position: relative; z-index: 2;
    }}
    .tile {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
      box-shadow: 0 12px 30px rgba(18, 48, 86, .10); padding: 14px 16px;
    }}
    .tile span {{ color: var(--muted); font-size: 10.5px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }}
    .tile strong {{ display: block; font-size: 22px; margin-top: 4px; }}
    .tile.score-good strong {{ color: var(--good); }}
    .tile.score-warning strong {{ color: var(--warn); }}
    .tile.score-danger strong {{ color: var(--bad); }}
    h2 {{ font-size: 17px; margin: 28px 0 10px; }}
    h3 {{ font-size: 14px; margin: 18px 0 6px; }}
    table {{
      background: var(--panel); border-collapse: collapse; width: 100%;
      border-radius: 14px; overflow: hidden; box-shadow: 0 8px 20px rgba(18, 48, 86, .06);
    }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 12px; text-align: left; vertical-align: top; }}
    thead th {{ background: #eaf1fa; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}
    tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
    th {{ width: 230px; font-weight: 700; }}
    td.code {{ font-family: ui-monospace, Consolas, monospace; font-weight: 700; }}
    .sev {{ border-radius: 999px; font-size: 11px; font-weight: 800; padding: 3px 9px; background: #eef3f9; }}
    .sev-high, .sev-serious {{ background: #fdecec; color: var(--bad); }}
    .sev-medium {{ background: #fff6e5; color: #9a6700; }}
    .sev-low {{ background: #e8f8f2; color: var(--good); }}
    ul {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; margin: 0; padding: 14px 14px 14px 32px; }}
    .ok {{ color: var(--good); font-weight: 700; }}
    .note {{ color: var(--muted); font-size: 12.5px; }}
    footer {{ color: var(--muted); font-size: 12px; margin-top: 28px; border-top: 1px solid var(--line); padding-top: 14px; }}
    @media print {{ body {{ background: #fff; }} .sheet {{ padding: 0; }} header.hero {{ border-radius: 0; }} }}
  </style>
</head>
<body>
  <div class="sheet">
    <header class="hero">
      <p class="eyebrow">Car OBD Diagnostics {escape(APP_VERSION)}</p>
      <h1>Vehicle Scan Report</h1>
      <p>Generated {escape(payload.get('created_at', '--'))}</p>
    </header>

    <section class="scorecard">
      <div class="tile score-{escape(score_status)}"><span>Health score</span><strong>{escape(str(score))}</strong></div>
      <div class="tile"><span>Stored codes</span><strong>{len(dtc.get('stored', []))}</strong></div>
      <div class="tile"><span>Pending codes</span><strong>{len(dtc.get('pending', []))}</strong></div>
      <div class="tile"><span>Protocol</span><strong>{escape(str(status.get('protocol', 'Unknown')))}</strong></div>
    </section>

    <h2>Overview</h2>
    <table>
      {row('Connection', 'Connected' if status.get('connected') else 'Not connected')}
      {row('Protocol', status.get('protocol', 'Unknown'))}
      {row('Adapter port', status.get('current_port') or 'auto')}
      {row('Adapter', status.get('adapter_name') or '--')}
      {row('Health verdict', health.get('headline', '--'))}
      {row('Battery / charging', battery.get('headline', '--'))}
      {row('Odometer (read from ECU)', f"{odometer.get('km')} km" if odometer.get('km') is not None else 'Not reported by this vehicle')}
    </table>

    <h2>Live Highlights</h2>
    <table>
      {row('RPM', vehicle.get('rpm', {}).get('value', '--'))}
      {row('Speed', vehicle.get('speed', {}).get('value', '--'))}
      {row('Coolant temp', vehicle.get('coolant_temp', {}).get('value', '--'))}
      {row('ECU voltage', vehicle.get('control_voltage', {}).get('value', '--'))}
      {row('Long fuel trim bank 1', vehicle.get('long_fuel_trim_1', {}).get('value', '--'))}
    </table>

    {code_rows('Stored Codes', dtc.get('stored', []))}
    {code_rows('Pending Codes', dtc.get('pending', []))}
    {code_rows('Permanent Codes', dtc.get('permanent', []))}

    <h2>Readiness</h2>
    <table>{readiness_rows}</table>

    <h2>Report Details</h2>
    {report_sections or '<p class="note">No report details available.</p>'}

    <footer>
      Standard OBD-II only. ABS, airbag and body modules may require brand-specific diagnostics.<br>
      Car OBD Diagnostics {escape(APP_VERSION)} - use this software at your own risk. Licensed under GNU GPLv3.
    </footer>
  </div>
</body>
</html>"""
