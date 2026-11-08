"""Thin bridge around the optional python-obd dependency.

Import errors are swallowed here once so the rest of the app can run in
demo mode on machines where python-obd (or its native serial dependency)
is not installed.
"""
import logging

try:
    import obd
    OBD_AVAILABLE = True
    OBD_IMPORT_ERROR = None
    # python-obd logs every failed connect/init/read attempt on its own,
    # separate from our friendly_message/log_error UI handling. It does
    # this from several separate per-module loggers (obd.obd, obd.elm327,
    # ...) that all inherit their *level* from this top-level "obd" logger,
    # but not its `disabled` flag - a child logger's own log calls ignore
    # an ancestor's `disabled` attribute entirely, so setting that alone
    # left "obd.elm327" critical messages (e.g. "Device disconnected while
    # reading") getting through. Setting the level above CRITICAL is
    # inherited by every child logger and actually filters everything.
    # The app retries connecting on a short loop while no adapter is
    # reachable, so left unfiltered this floods the console every retry;
    # connection state is already surfaced through the UI (System page
    # error log) instead.
    obd.logger.setLevel(logging.CRITICAL + 1)
except Exception as e:
    obd = None
    OBD_AVAILABLE = False
    OBD_IMPORT_ERROR = e


def get_command(name):
    if not OBD_AVAILABLE:
        return None

    return getattr(obd.commands, name, None)
