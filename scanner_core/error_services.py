"""Error normalisation, the shared in-memory error list and the rotating
error log file.

friendly_message() turns raw exception text into a short, workshop-friendly
message. log_error() records it for the System page, prints a de-duplicated
line to the console and appends it to logs/scanner-errors.log, which rotates
so it cannot grow without bound. The in-memory list is what the UI shows;
the file is what you read when a problem happened an hour ago.

Not every error is a connection problem. A single unsupported PID should not
repaint the whole dashboard as broken, so only errors that really describe
the connection state update obd_status["error"]/["user_message"]; everything
else is logged and listed without touching the headline status.
"""
import logging
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

from config import (
    ERROR_LOG_BACKUP_COUNT,
    ERROR_LOG_FILENAME,
    ERROR_LOG_MAX_BYTES,
    LOG_DIR_NAME,
    RECENT_ERROR_MEMORY_LIMIT,
)
from scanner_core import app_state

# Sources whose failure genuinely describes the adapter/ECU link. Anything
# else is incidental and must not overwrite the connection status.
CONNECTION_SOURCES = {
    "Connect OBD",
    "Reconnect OBD",
    "Change COM port",
    "Close OBD connection",
    "Update loop",
}

_error_state_lock = threading.Lock()
_logger = None
_logger_lock = threading.Lock()


def _get_logger():
    global _logger
    with _logger_lock:
        if _logger is not None:
            return _logger

        logger = logging.getLogger("obd_scanner.errors")
        logger.setLevel(logging.ERROR)
        logger.propagate = False

        try:
            log_dir = app_state.PROJECT_ROOT / LOG_DIR_NAME
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / ERROR_LOG_FILENAME,
                maxBytes=ERROR_LOG_MAX_BYTES,
                backupCount=ERROR_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
            logger.addHandler(handler)
        except Exception:
            # A read-only install directory should not stop the scanner; the
            # in-memory list and the console still work.
            logger.addHandler(logging.NullHandler())

        _logger = logger
        return _logger


def is_known_port_config_error(error):
    message = str(error or "").lower()
    return (
        "cannot configure port" in message
        or "the parameter is incorrect" in message
        or "oserror(22" in message
    )


def friendly_message(error=None, source=None, port=None):
    from scanner_core.settings_services import get_configured_port

    raw_message = str(error or "").strip()
    message = raw_message.lower()
    target_port = port or get_configured_port()
    port_label = target_port or "the selected port"

    if (
        "the parameter is incorrect" in message
        or "oserror(22" in message
        or "cannot configure port" in message
    ):
        return "Cannot connect to the ECU. The OBD adapter is not connected or not detected."

    if source in {"Connect OBD", "Reconnect OBD"}:
        if "could not open port" in message or "filenotfounderror" in message:
            return f"No adapter found on {port_label}. Check the cable and adapter port."
        if "access is denied" in message or "permissionerror" in message or "toegang geweigerd" in message:
            return f"{port_label} is busy or blocked by another app. Close other OBD software and try again."
        if "unable to connect" in message or "not connected" in message:
            return "Adapter found, but the car is not responding. Turn ignition on and try again."
        if "no obd connection found" in message:
            return "No OBD connection found. Check the adapter, ignition, and selected adapter port."
        if "ecu did not answer" in message:
            return "The adapter answered but the ECU did not. Turn the ignition on and try again."
        if "python-obd did not load" in message:
            return "The OBD library could not start. Reinstall the scanner dependencies."

    if source == "Live data query":
        return "Live data could not be read. Check the ignition and reconnect if needed."

    if source == "Read DTC":
        return "Fault codes could not be read right now. Try reconnecting and scan again."

    if source == "Clear fault codes":
        return "Clearing fault codes failed. Keep ignition on and try once more."

    if source == "Change COM port":
        return "Cannot connect to the ECU. Check the OBD adapter."

    if source in {"Initialize config database", "Read config", "Save config"}:
        return "Scanner settings could not be loaded or saved."

    if source == "Read VIN":
        return "VIN could not be read from the car. Some cars do not expose it over standard OBD."

    if source == "Decode VIN":
        return "VIN was read, but online vehicle details could not be loaded."

    if source == "Lookup RDW":
        return "RDW lookup failed. Check the plate and your internet connection."

    if source == "Save photo attachment":
        return "The photo could not be saved. Check the file type and size."

    if raw_message:
        return raw_message

    return "An unknown scanner error occurred."


def _write_to_file(source, raw_message, friendly):
    try:
        detail = f"{source}: {raw_message}"
        if friendly and friendly != raw_message:
            detail += f" | {friendly}"
        _get_logger().error(detail)
    except Exception:
        pass


def log_error(source, error, affects_status=None):
    """Record an error for the UI, the console and the log file.

    affects_status defaults to "only when this source describes the
    connection", which keeps an unsupported sensor from making a healthy
    session look broken.
    """
    console_message = friendly_message(error, source=source)
    raw_message = str(error)
    signature = f"{source}|{console_message or raw_message}"
    now = time.time()

    with _error_state_lock:
        last_seen = app_state.error_log_state.get(signature, 0)
        should_emit_console = (now - last_seen) >= 12
        if should_emit_console:
            app_state.error_log_state[signature] = now
        # Keep the de-duplication table from growing forever on a long run.
        if len(app_state.error_log_state) > 256:
            cutoff = now - 600
            app_state.error_log_state = {
                key: seen for key, seen in app_state.error_log_state.items() if seen >= cutoff
            }

    if should_emit_console:
        if is_known_port_config_error(error):
            print(f"{source}: Cannot connect to the ECU. The OBD adapter is not connected or not detected.")
        else:
            print(f"{source}: {raw_message}")
            if console_message and console_message != raw_message:
                print(f"{source} (friendly): {console_message}")
            if isinstance(error, BaseException):
                traceback.print_exc()
        _write_to_file(source, raw_message, console_message)

    if affects_status is None:
        affects_status = source in CONNECTION_SOURCES

    timestamp = time.strftime("%H:%M:%S")
    with app_state.state_lock:
        if affects_status:
            app_state.obd_status["error"] = raw_message
            app_state.obd_status["user_message"] = console_message
        app_state.obd_status["last_update"] = timestamp

        recent = app_state.obd_status["recent_errors"]
        existing = recent[0] if recent else None
        if existing and existing.get("source") == source and existing.get("technical_message") == raw_message:
            existing["time"] = timestamp
            existing["message"] = console_message
            existing["count"] = int(existing.get("count", 1)) + 1
        else:
            technical_message = (
                "Suppressed repeated serial port configuration error."
                if is_known_port_config_error(error)
                else raw_message
            )
            recent.insert(0, {
                "time": timestamp,
                "source": source,
                "message": console_message,
                "technical_message": technical_message,
                "count": 1,
            })
            del recent[RECENT_ERROR_MEMORY_LIMIT:]


def read_error_log_tail(max_lines=200):
    """Return the last lines of the rotating error log, newest last."""
    path = app_state.PROJECT_ROOT / LOG_DIR_NAME / ERROR_LOG_FILENAME
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        return [line.rstrip("\n") for line in lines[-max_lines:]]
    except OSError:
        return []
