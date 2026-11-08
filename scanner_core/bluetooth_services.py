"""Guided OS-pairing Bluetooth support for classic-Bluetooth (SPP) ELM327
adapters on Linux. The adapter must already be paired at the OS level
(Settings > Bluetooth or `bluetoothctl`); this module only lists the paired
devices and binds one to an /dev/rfcommN serial device with `rfcomm bind`, so
the existing serial connection path (python-obd over a plain serial port)
picks it up unmodified. Windows and macOS pair Bluetooth SPP adapters
directly to a COM port / /dev/tty.* device at the OS level and need no
rfcomm bind step, so this module is a no-op there.
"""
import os
import platform
import re
import subprocess

MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
RFCOMM_DEVICE_PATTERN = re.compile(r"^/dev/rfcomm(\d+)$")

IS_LINUX = platform.system() == "Linux"


def bluetooth_bind_supported():
    return IS_LINUX


def list_paired_bluetooth_devices():
    if not IS_LINUX:
        return []

    try:
        result = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    devices = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) != 3 or parts[0] != "Device":
            continue
        mac, name = parts[1], parts[2]
        if MAC_PATTERN.match(mac):
            devices.append({"mac": mac.upper(), "name": name.strip() or mac})

    return devices


def _next_free_rfcomm_index():
    for index in range(30):
        if not os.path.exists(f"/dev/rfcomm{index}"):
            return index
    return None


def bind_rfcomm_device(mac, channel=1):
    if not IS_LINUX:
        return None, "Bluetooth adapter binding is only supported on Linux."

    mac = str(mac or "").strip().upper()
    if not MAC_PATTERN.match(mac):
        return None, "That does not look like a valid Bluetooth MAC address."

    try:
        channel = int(channel)
    except (TypeError, ValueError):
        channel = 1
    if channel < 1 or channel > 30:
        channel = 1

    index = _next_free_rfcomm_index()
    if index is None:
        return None, "No free /dev/rfcomm device slot was found."

    device = f"/dev/rfcomm{index}"
    try:
        result = subprocess.run(
            ["rfcomm", "bind", str(index), mac, str(channel)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        return None, "The rfcomm tool was not found. Install bluez-utils/rfcomm and try again."
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"Could not run rfcomm: {e}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return None, f"rfcomm bind failed: {detail or 'unknown error'}. It may need to be run with elevated permissions."

    return device, ""


def release_rfcomm_device(device):
    if not IS_LINUX:
        return False, "Bluetooth adapter binding is only supported on Linux."

    match = RFCOMM_DEVICE_PATTERN.match(str(device or "").strip())
    if not match:
        return False, "That does not look like an /dev/rfcommN device."

    index = match.group(1)
    try:
        result = subprocess.run(
            ["rfcomm", "release", index],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        return False, "The rfcomm tool was not found."
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Could not run rfcomm: {e}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, f"rfcomm release failed: {detail or 'unknown error'}."

    return True, ""
