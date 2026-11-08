"""Shareable read-only report links: turn a saved scan into a public,
unguessable URL that opens the same HTML report export - no login, no app
install - so a seller can hand a buyer a link instead of a file. The link
only works as far as this app itself is reachable (localhost stays
localhost-only; a LAN or public server carries the same reach the rest of
the dashboard already has)."""
import secrets

from scanner_core import app_state
from scanner_core.storage_services import (
    get_scan_by_share_token as storage_get_scan_by_share_token,
    get_scan_share_token as storage_get_scan_share_token,
    scan_exists as storage_scan_exists,
    set_scan_share_token as storage_set_scan_share_token,
)


def scan_exists(scan_id):
    return storage_scan_exists(app_state.DB_PATH, int(scan_id))


def create_or_get_share_link(scan_id):
    """Return the scan's existing share token, or mint a new one.

    The token is read from the database rather than taken from the caller, so
    a client cannot pick its own (guessable) token.
    """
    existing = storage_get_scan_share_token(app_state.DB_PATH, int(scan_id))
    if existing:
        return existing

    token = secrets.token_urlsafe(24)
    if not storage_set_scan_share_token(app_state.DB_PATH, int(scan_id), token):
        return None
    return token


def revoke_share_link(scan_id):
    return storage_set_scan_share_token(app_state.DB_PATH, int(scan_id), None)


def get_shared_scan(token):
    if not token:
        return None
    return storage_get_scan_by_share_token(app_state.DB_PATH, token)
