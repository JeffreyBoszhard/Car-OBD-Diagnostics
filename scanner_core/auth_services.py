"""Optional PIN lock for the dashboard.

A workshop tablet on a garage network is reachable by everyone else on that
network, and this dashboard can clear fault codes. Setting ACCESS_PIN in
config.py (or OBD_PIN in the environment) puts a PIN screen in front of
everything; leaving it empty keeps the dashboard wide open, which is the
right default for a laptop plugged straight into one car.

The unlock is kept in a signed cookie, so no server-side session store is
needed. The signature uses SECRET_KEY; when that is left empty a random key
is generated per start, which simply means a restart asks for the PIN again.
"""
import hashlib
import hmac
import secrets
import time

from config import ACCESS_PIN, ACCESS_SESSION_MAX_AGE, SECRET_KEY

COOKIE_NAME = "obd_unlock"

# Paths that must stay reachable while locked: the unlock screen itself, the
# unlock endpoint and the static assets that screen is built from.
PUBLIC_ENDPOINTS = {"static", "lock_screen", "api_unlock"}

_runtime_secret = SECRET_KEY or secrets.token_hex(32)


def pin_required():
    return bool(ACCESS_PIN)


def _sign(issued_at):
    message = f"{issued_at}".encode()
    digest = hmac.new(_runtime_secret.encode(), message, hashlib.sha256).hexdigest()
    return f"{issued_at}.{digest}"


def issue_token():
    return _sign(int(time.time()))


def token_is_valid(token):
    if not pin_required():
        return True

    raw = str(token or "")
    if "." not in raw:
        return False

    issued_text, _, _digest = raw.partition(".")
    try:
        issued_at = int(issued_text)
    except ValueError:
        return False

    expected = _sign(issued_at)
    if not hmac.compare_digest(expected, raw):
        return False

    age = time.time() - issued_at
    return 0 <= age <= ACCESS_SESSION_MAX_AGE


def pin_is_correct(candidate):
    """Constant-time PIN comparison, so a wrong guess leaks no timing hint."""
    if not pin_required():
        return True
    return hmac.compare_digest(str(candidate or "").strip(), ACCESS_PIN)
