# Car-OBD-Diagnostics

A Python and Flask based OBD-II workshop scanner dashboard for reading live ECU data, diagnostic trouble codes, readiness information, freeze-frame data, vehicle details and local garage notes through a USB OBD adapter.

The dashboard is designed like a rugged workshop scan tablet: dark diagnostic panels, individual pages for each scanner function, consistent pill-style buttons, English/Dutch language support, local SQLite storage, demo mode and update/changelog popups.

Current version: `v0.6.0`

## Features

- Rugged workshop scanner style web UI with separate layouts for every page
- Home launcher with scanner app tiles
- Live RPM and speed gauges with fast gauge polling through `/api/gauges`
- Live sensor stream with coolant, voltage, fuel trim, engine load and throttle charts
- Graph recording with HTML playback export
- Stored, pending and permanent DTC views
- Full vehicle diagnostic scan progress with per-part status and final summary
- Fault-code clearing with SAFE mode protection
- Readiness monitor and freeze-frame views
- Experimental VIN reading and manual VIN lookup
- Dutch RDW license plate lookup
- Vehicle lookup history stored in browser local storage
- Local scan history stored in SQLite
- Garage notes per VIN/license plate, with search, edit, delete and HTML export
- Garage note validation for VIN, license plate and note text
- USB / COM port selection with custom dropdown and native select fallback
- Connection test and adapter status view
- Connection quality indicators for adapter, port, vehicle and live data
- Demo mode with idle, cruise, heavy-load and fault-present presets
- Polling profiles: Performance, Balanced and Safe
- Styled scan report export in full, fault-code, live-data or vehicle-info mode
- Multi-language report export in English or Dutch
- Battery and charging voltage check
- Optional simple summary mode
- Reset UI cache action for browser-side state
- Automatic GitHub update check with dashboard popup when a newer version exists
- Changelog popup on first load after each local version bump
- Supported PID overview

## Important OBD-II Note

This app uses standard OBD-II data through `python-obd`. Standard OBD-II mainly covers engine and emissions related ECU data.

ABS, airbag, BCM, window, mirror, odometer, ADAS and other manufacturer-specific module access usually requires brand-specific diagnostics, UDS/CAN tooling, security access and vehicle-specific CAN IDs. Those features are not guaranteed through this project or the `python-obd` library.

Do not use the dashboard while driving. Have another person operate it, or use it only while parked.

## Requirements

- Python 3.10 or newer recommended
- USB OBD-II adapter, for example an ELM327-compatible USB adapter
- Vehicle with an OBD-II port
- Windows, macOS or Linux

Python packages:

```txt
flask
obd
pyserial
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

Start the Flask app:

```bash
python app.py
```

Open:

```txt
http://127.0.0.1:5000/
```

The app runs on port `5000` by default.

## Using A Real OBD Adapter

1. Plug the USB OBD-II adapter into your computer.
2. Plug the adapter into the vehicle OBD-II port.
3. Turn the ignition on.
4. Open the dashboard.
5. Go to `Service`.
6. Select the detected COM port, or leave it on auto-detect.
7. Use `Test Connection` or `Retry Connection`.

On Windows, the adapter usually appears as `COM3`, `COM4`, `COM5`, etc.

## Demo Mode

Demo mode lets you test the UI without a USB adapter or vehicle.

Go to `Service`, enable `Demo Mode`, then choose a preset:

- Idle
- Cruise
- Heavy Load
- Fault Present

Demo mode generates simulated live data, fault codes, readiness values and vehicle information.

## Update System

The dashboard has two update-related popups:

- Changelog popup: shown after installing a new local app version.
- GitHub update popup: shown when a newer version is available on GitHub.

The GitHub update notification opens the project download page. Updates are not installed automatically.

Manual update from a Git clone:

```bash
git pull
python -m pip install -r requirements.txt
python app.py
```

## HTML Report Export

The dashboard can export a styled HTML scan report with vehicle details, live data, DTCs, readiness information and freeze-frame data where available.

Report export modes:

- Full report
- Fault codes only
- Live data only
- Vehicle info only

Reports follow the selected interface language.

## Garage Notes

Garage notes are stored locally in SQLite and linked to a vehicle identity. A note requires both:

- VIN
- License plate

Garage notes support:

- Search
- Edit
- Delete with confirmation
- HTML export
- Optional photo attachment saved in the note payload

## Configuration

Refresh timings, update URLs and history limits can be adjusted in `config.py`.

```python
APP_VERSION = "v0.6.0"
POLL_INTERVAL = 0.1
RPM_POLL_INTERVAL = 0.05
OBD_CONNECT_TIMEOUT = 0.6
OBD_CONNECT_ATTEMPTS = 2
OBD_CONNECT_RETRY_DELAY = 0.25
MAX_POLL_INTERVAL = 0.8
STALE_AFTER_SECONDS = 0.9
SCAN_HISTORY_LIMIT = 20
UPDATE_CHECK_CONFIG_URL = "https://raw.githubusercontent.com/JeffreyBoszhard/Car-OBD-Diagnostics/main/config.py"
UPDATE_DOWNLOAD_URL = "https://github.com/JeffreyBoszhard/Car-OBD-Diagnostics"
```

Lower polling values feel more live, but they query the ECU more often. Increase timings if an adapter becomes unstable.

## Language Support

The interface supports:

- English
- Dutch

The app loads:

- `static/en_app.js`
- `static/nl_app.js`

The selected language is stored in the `obd_lang` browser cookie.

## Project Structure

```txt
.
|-- app.py
|-- changelog.py
|-- config.py
|-- requirements.txt
|-- scanner_core/
|   |-- cache_services.py
|   |-- demo_services.py
|   |-- dtc_catalog.py
|   |-- garage_services.py
|   |-- obd_services.py
|   |-- report_services.py
|   |-- session_services.py
|   |-- storage_services.py
|   `-- translation.py
|-- static/
|   |-- en_app.js
|   |-- nl_app.js
|   `-- style.css
`-- templates/
    |-- dashboard.html
    |-- pages/
    `-- partials/
```

## Local Data

The app stores local configuration, scan history and garage notes in:

```txt
scanner_config.db
```

Browser-side UI state and VIN/license plate lookup history are stored in `localStorage`.

Use `Reset UI Cache` on the System page if the browser keeps old dashboard state after an update. This does not delete saved scans or garage notes from `scanner_config.db`.

## Known Limitations

- VIN detection can be unreliable on some cars or adapter response formats.
- Fault-code reading and clearing have not been fully tested on many real vehicles yet.
- Standard OBD-II does not guarantee ABS, airbag, BCM, ADAS, odometer or manufacturer-specific module access.
- Live RPM and speed responsiveness depends on ECU, adapter, serial connection, Python polling and browser rendering.
- Dutch RDW license plate lookup only supports Dutch plates.
- Auto-update is not implemented yet; the dashboard currently only notifies and links to GitHub.

## Troubleshooting

If no adapter is detected:

- Check the USB cable
- Check Device Manager for the COM port on Windows
- Try another USB port
- Confirm the ignition is on
- Select the correct COM port in `Service`
- Try disabling other software that may be using the adapter

If PowerShell blocks `.venv\Scripts\activate`:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

If Windows shows `PermissionError(13)` or `Access denied` for a COM port:

- Another app or Python process may already be using the adapter
- Close other OBD/serial tools
- Stop duplicate `python app.py` processes
- Unplug and reconnect the USB adapter
- Try a different COM port if Windows assigned a new one

If live data is empty or unstable:

- Confirm the vehicle supports standard OBD-II
- Try reconnecting
- Try the Safe polling profile
- Check adapter quality
- Increase refresh intervals in `config.py`

## Safety And Disclaimer

Be careful when clearing fault codes. Clearing DTCs can remove diagnostic evidence that may be useful for repair work. The app includes SAFE mode protection to prevent accidental clearing.

This project is provided for educational and personal diagnostic use. Use it at your own risk. The author is not responsible for damage, data loss, broken adapters, vehicle issues, incorrect diagnostics, cleared fault codes, repair costs or any other problems caused directly or indirectly by using this software.

## License

This project is licensed under the GNU General Public License v3.0.

You may use, modify, share and distribute this project under the terms of the GPLv3. If you distribute modified versions, you must also provide the source code under the same license.





