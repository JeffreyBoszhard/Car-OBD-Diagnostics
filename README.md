# Car-OBD-Diagnostics

A Python and Flask based OBD-II workshop scanner dashboard for reading live ECU data, diagnostic trouble codes, readiness information, freeze-frame data, vehicle details and a local car journal through a USB or Bluetooth OBD adapter.

The dashboard is built to look and work like a professional diagnostic scan tool: an instrument housing, modules per scanner function, mono readouts for live values, real status LEDs, and smooth animations that follow a Light/Dark/Auto theme. All data stays on your own machine in a single local `.db` file.

Current version: `v0.9.0`

## Features

### Live data
- Live RPM and speed gauges with a dial rim, redline band and a smoothed needle sweep
- Live data pushed over a server-sent events stream, so one frame is built per tick no matter how many tabs are open
- Live sensor stream with coolant, voltage, fuel trim, engine load and throttle charts, plus an inline sparkline per numeric sensor
- Trip computer: distance, average and max speed, max RPM, driving vs idle time, average coolant temperature
- Estimated fuel economy (live and session average) from MAF airflow and speed, and a 0-100 km/h / 0-60 mph timer
- Graph recording with HTML playback export
- Freeze-stream button to hold the current frame for review

### Diagnostics
- Stored, pending and permanent DTC views with severity, affected system and likely causes
- Full vehicle diagnostic scan with per-step progress, a travelling scanline on the active step and a final summary
- Fault-code clearing, always available, always behind an explicit confirmation that states what clearing does
- Readiness monitor and freeze-frame views
- On-board monitor test results (Mode 06) where the adapter and vehicle expose them
- Odometer read from the ECU (PID A6), DPF values and hybrid/EV battery pack state, where the vehicle answers them
- Supported PID overview
- Battery and charging voltage check
- Purchase-check health report with score and checklist
- Optional browser notification when a new fault code appears

### Vehicle identity
- VIN reading over OBD, with manual VIN entry as a fallback
- VIN decoding through the NHTSA API, cached locally
- Dutch RDW license plate lookup
- Vehicle lookup history in browser local storage

### Records
- Local scan history with styled HTML or CSV report export in full, fault-code, live-data or vehicle-info mode
- Shareable read-only report links, revocable per scan
- Car journal: repair notes, trips and fuel-ups per vehicle, with search, edit, delete and HTML export
- Photo attachments on journal notes, stored as files and validated by content
- Fuel-up log with a per-fill economy trend
- Automatic maintenance reminder, using the odometer read from the car when available
- Optional vehicle profiles (nickname, plate, VIN, brand, model, year, tank size)

### Setup and connection
- Adapter port selection with port scoring, so the most adapter-looking ports are tried first
- ECU verification before a connection is reported as working
- Five-stage connection test, including battery voltage measured at the port
- Bluetooth adapter support, with a one-click rfcomm bind flow on Linux
- Demo mode with idle, cruise, heavy-load, fault-present and everything-wrong presets
- Polling profiles: Performance, Balanced, Economy and Debug
- Light / Dark / Auto theme, Metric / Imperial units, and five interface languages
- Optional PIN lock in front of the whole dashboard
- Automatic GitHub update check with an in-app notification, and a changelog popup after a local version bump

## Important OBD-II Note

This app uses standard OBD-II data through `python-obd`. Standard OBD-II mainly covers engine and emissions related ECU data.

ABS, airbag, BCM, window, mirror, ADAS and other manufacturer-specific module access usually requires brand-specific diagnostics, UDS/CAN tooling, security access and vehicle-specific CAN IDs. Those features are not guaranteed through this project or the `python-obd` library.

The odometer, DPF and battery-pack readings use standard service modes, but support is far from universal. A value that is not shown simply was not reported by the vehicle.

Do not use the dashboard while driving. Have another person operate it, or use it only while parked.

## Requirements

- Python 3.10 or newer
- An OBD-II adapter, for example an ELM327-compatible USB or Bluetooth adapter
- A vehicle with an OBD-II port
- Windows, macOS or Linux

Python packages (see `requirements.txt`):

```txt
Flask>=3.0,<4.0
obd>=0.7.2,<0.8
pyserial>=3.5,<4.0
```

## Installation

Clone the repository:

```bash
git clone https://github.com/JeffreyBoszhard/Car-OBD-Diagnostics.git
cd Car-OBD-Diagnostics
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Running The App

```bash
python app.py
```

Then open:

```txt
http://127.0.0.1:5000/
```

The app listens on port `5000` by default and is reachable from other devices on the same network, which is what makes it usable from a tablet or phone in the workshop.

### Environment options

| Variable | Default | What it does |
| --- | --- | --- |
| `OBD_HOST` | `0.0.0.0` | Interface to listen on. Use `127.0.0.1` to keep the dashboard on this machine only. |
| `OBD_HTTP_PORT` | `5000` | Web port. Named `HTTP` because "OBD port" already means the adapter port everywhere else. |
| `OBD_PIN` | empty | When set, the dashboard asks for this PIN once per browser. Empty means no lock. |
| `OBD_SECRET_KEY` | empty | Signs the unlock cookie. Empty means a random key per start, so a restart asks for the PIN again. |
| `OBD_DEBUG` | `0` | Flask debug mode. Leave off: the debugger allows running code on this machine. |

Example, a locked scanner on a workshop tablet:

```bash
OBD_PIN=4321 OBD_SECRET_KEY=some-long-random-string python app.py
```

## Using A Real OBD Adapter

1. Plug the OBD-II adapter into your computer (or pair it over Bluetooth).
2. Plug the adapter into the vehicle OBD-II port.
3. Turn the ignition on.
4. Open the dashboard.
5. Go to `Service`.
6. Select the detected adapter port, or leave it on auto-detect.
7. Use `Test Connection` or `Retry Connection`.

The app only reports a working connection once the ECU behind the adapter answers a real PID. If the adapter answers but the car does not, it says so instead of claiming success - that almost always means the ignition is off.

### Bluetooth adapters

Pair the adapter at the operating-system level first. On Windows and macOS it then appears as a normal serial port and can be picked from the adapter port list. On Linux, use `Scan Paired Devices` and `Bind & Use This Adapter` on the Service page to bind it to an `/dev/rfcommN` device.

## Configuration

`config.py` holds the version, polling intervals, connection timeouts, cache lifetimes, upload limits and the update-check URLs. Every interval is in seconds.

```python
APP_VERSION = "v0.9.0"

POLL_INTERVAL = 0.1        # main live-data loop
RPM_POLL_INTERVAL = 0.05   # fast gauge loop
STREAM_INTERVAL = 0.2      # how often a full frame is pushed to the browser
```

Lower polling values feel more live, but they query the ECU more often. Increase them if an adapter becomes unstable.

Warning thresholds for coolant temperature, voltage, engine load, throttle and fuel trim live in `dashboard_runtime.json` and can be edited from the System page. The health checklist and the live sensor highlighting both use those same numbers.

## Language Support

The interface and everything the app generates are available in:

- English
- Dutch
- German
- Spanish
- Polish

The selected language is stored in the `obd_lang` browser cookie and in `dashboard_setup.json`. Interface strings live in `scanner_core/translation.py`; the sentences the Python side produces at runtime live in `scanner_core/server_text.py`.

## Project Structure

```txt
.
|-- app.py                     Flask routes and startup
|-- changelog.py               In-app changelog entries
|-- config.py                  Version, intervals, limits, URLs
|-- requirements.txt
|-- requirements-dev.txt
|-- pyproject.toml             ruff and pytest configuration
|-- scanner_core/
|   |-- app_state.py           Shared runtime state and locks
|   |-- attachment_services.py Photo uploads, validated and stored on disk
|   |-- auth_services.py       Optional PIN lock
|   |-- background_loops.py    Poll loops and the live-data publisher
|   |-- bluetooth_services.py  Paired-device listing and rfcomm binding
|   |-- connection_services.py Connect, verify ECU, query defensively
|   |-- demo_services.py       Simulated vehicle for demo mode
|   |-- dtc_catalog.py         Fault-code lookup
|   |-- dtc_services.py        Code reading and the full vehicle scan
|   |-- errorcode.py           Fault-code reference data
|   |-- error_services.py      Friendly messages and the rotating error log
|   |-- event_services.py      Server-sent events broker
|   |-- extended_pid_services.py Odometer, DPF and battery-pack PIDs
|   |-- fuel_log_services.py   Fuel-ups and economy trend
|   |-- garage_services.py     Car journal validation, filtering and export
|   |-- health_services.py     Health report, battery check, PID support
|   |-- live_data_services.py  Live value tracking and the poll guard
|   |-- maintenance_services.py Service estimates
|   |-- obd_bridge.py          Thin wrapper over python-obd
|   |-- obd_services.py        Adapter and ECU detection
|   |-- report_services.py     Purchase report
|   |-- report_share_services.py Shareable report links
|   |-- scan_payload_services.py Scan payload, storage and exports
|   |-- server_text.py         Server-generated text, translated
|   |-- session_services.py    Session state for the UI
|   |-- settings_services.py   Cached settings store
|   |-- setup_services.py      Setup/runtime JSON state, update check
|   |-- storage_services.py    SQLite storage
|   |-- translation.py         Interface translations
|   |-- trip_log_services.py   Saved trips
|   |-- trip_services.py       Live trip computer
|   |-- units_services.py      Metric/imperial conversion
|   |-- vehicle_services.py    Vehicle profiles
|   `-- vin_services.py        VIN reading, decoding, RDW lookup
|-- static/
|   |-- app.js
|   `-- style.css
|-- templates/
|   |-- dashboard.html
|   |-- lock.html
|   |-- pages/
|   `-- partials/
`-- tests/
```

## Local Data

| Path | Contents |
| --- | --- |
| `scanner_config.db` | Settings, saved scans, car journal, fuel-ups, trips, vehicles |
| `dashboard_setup.json` | Language and units |
| `dashboard_runtime.json` | Warning thresholds and fuel-trim history |
| `uploads/` | Car-journal photos |
| `logs/` | Rotating error log |

Browser-side UI state and VIN/plate lookup history are stored in `localStorage`. Use `Reset UI Cache` on the System page if the browser keeps old dashboard state after an update; it does not delete anything from `scanner_config.db`.

Nothing is uploaded anywhere. The only outbound requests the app makes are the GitHub version check, the NHTSA VIN decode and the RDW plate lookup, and only when those features are used.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q     # tests
python -m ruff check .        # lint
```

Both run in CI on every push. The lint step is there for a reason: the MySQL backend this release removed shipped for two versions calling a function from a module it never imported, and reaching that code needed a MySQL server - so no manual test ever hit it. A linter sees it without one.

The tests cover unit conversion, version comparison, VIN and plate validation, the storage layer against a temporary database, attachment validation, the PIN lock, and the HTTP surface. They need no adapter and no vehicle.

## Known Limitations

- VIN detection can be unreliable on some cars or adapter response formats.
- Fault-code reading and clearing have not been fully tested on many real vehicles yet.
- Standard OBD-II does not guarantee ABS, airbag, BCM or ADAS module access.
- The odometer, DPF and battery-pack PIDs are not supported by every vehicle, and the DPF pressure layout is manufacturer-specific, so it is shown as raw bytes.
- Live RPM and speed responsiveness depends on the ECU, the adapter, the serial connection and browser rendering.
- RDW license plate lookup only supports Dutch plates.
- Auto-update is not implemented; the dashboard only notifies and links to GitHub.
- The app runs on Flask's development server. That is fine for one workshop machine; put it behind a production WSGI server if you expose it more widely.

## Troubleshooting

If no adapter is detected:

- Check the USB cable, and try another USB port
- Check Device Manager for the COM port on Windows
- Confirm the ignition is on
- Select the correct adapter port on the `Service` page
- Close other software that may be holding the adapter

If PowerShell blocks `.venv\Scripts\activate`:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

If Windows shows `PermissionError(13)` or `Access denied` for a port:

- Another app or Python process may already be using the adapter
- Close other OBD/serial tools and stop duplicate `python app.py` processes
- Unplug and reconnect the adapter
- Try a different port if Windows assigned a new one

If the connection test says the adapter responds but the ECU does not:

- Turn the ignition on (engine does not have to run)
- Check that the adapter is fully seated in the OBD-II port
- Some vehicles only wake the bus with the key in position II

If live data is empty or unstable:

- Confirm the vehicle supports standard OBD-II
- Try reconnecting
- Try the Economy polling profile
- Increase the refresh intervals in `config.py`

Past problems are recorded in `logs/scanner-errors.log`, and the recent ones are listed on the System page.

## Safety And Disclaimer

Be careful when clearing fault codes. Clearing erases stored codes, freeze-frame data and the readiness monitors, which can remove diagnostic evidence that is useful for repair work - and it does not repair the fault. The dashboard always asks for confirmation first and spells out what will happen.

Use this software at your own risk.

## License

Licensed under the GNU General Public License v3.0. See `LICENSE`.
