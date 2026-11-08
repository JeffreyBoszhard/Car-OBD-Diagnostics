"""Garage-note photo attachments, stored as files instead of base64.

A photo used to be read in the browser as a data: URI and posted along with
the note, where it ended up inside the note's saved scan payload in the
database. A single phone photo added several megabytes to that row, and the
car-journal list then shipped every one of those photos to the browser again
on every refresh - and the raw URI was written straight into an href, so a
crafted value could inject markup into the page.

Now the browser posts the file itself, the bytes are validated here, and the
note stores only a generated filename. The dashboard loads the photo from
/uploads/<name> like any other image.

Validation is deliberately strict: the extension comes from the sniffed
content type (never from the uploaded filename), names are generated rather
than taken from the client, and anything that is not a real image of an
allowed type is refused.
"""
import binascii
import base64
import re
import uuid

from config import UPLOAD_ALLOWED_TYPES, UPLOAD_MAX_BYTES
from scanner_core import app_state

ATTACHMENT_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png|webp|gif)$")

DATA_URI_PATTERN = re.compile(r"^data:(?P<mime>[\w./+-]+);base64,(?P<data>.+)$", re.DOTALL)

# Magic bytes, so the stored type is what the file actually is rather than
# whatever content type the browser claimed.
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def ensure_upload_dir():
    app_state.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return app_state.UPLOAD_DIR


def sniff_image_type(data):
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    # WebP: "RIFF" .... "WEBP"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_valid_attachment_name(name):
    """Only names this module generated are ever served or deleted."""
    return bool(ATTACHMENT_NAME_PATTERN.match(str(name or "")))


def attachment_path(name):
    if not is_valid_attachment_name(name):
        return None
    return app_state.UPLOAD_DIR / name


def save_attachment_bytes(data):
    """Validate and store raw image bytes. Returns (name, error)."""
    if not data:
        return None, "The photo was empty."

    if len(data) > UPLOAD_MAX_BYTES:
        limit_mb = UPLOAD_MAX_BYTES / (1024 * 1024)
        return None, f"The photo is larger than the {limit_mb:.0f} MB limit."

    mime = sniff_image_type(data)
    if mime is None or mime not in UPLOAD_ALLOWED_TYPES:
        return None, "Only JPEG, PNG, WebP or GIF photos can be attached."

    ensure_upload_dir()
    name = f"{uuid.uuid4().hex}{UPLOAD_ALLOWED_TYPES[mime]}"
    path = app_state.UPLOAD_DIR / name
    try:
        path.write_bytes(data)
    except OSError as e:
        return None, f"The photo could not be written to disk: {e}"

    return name, ""


def save_attachment_from_upload(file_storage):
    """Store a multipart file upload. Returns (name, error)."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        return None, ""

    data = file_storage.read(UPLOAD_MAX_BYTES + 1)
    if len(data) > UPLOAD_MAX_BYTES:
        limit_mb = UPLOAD_MAX_BYTES / (1024 * 1024)
        return None, f"The photo is larger than the {limit_mb:.0f} MB limit."

    return save_attachment_bytes(data)


def save_attachment_from_data_uri(value):
    """Accept a legacy data: URI body and store it as a file.

    Older dashboard versions posted photos this way; keeping this path means
    an outdated cached page, or a script someone wrote against the old API,
    still works instead of silently losing the photo.
    """
    match = DATA_URI_PATTERN.match(str(value or "").strip())
    if not match:
        return None, "The photo format was not recognised."

    try:
        data = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError):
        return None, "The photo data was not valid base64."

    return save_attachment_bytes(data)


def delete_attachment(name):
    path = attachment_path(name)
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def prune_orphan_attachments(known_names):
    """Delete stored photos no garage note references any more.

    Run after a note is deleted. Keeps the uploads folder from collecting
    files forever.
    """
    directory = app_state.UPLOAD_DIR
    if not directory.exists():
        return 0

    removed = 0
    try:
        for path in directory.iterdir():
            if not path.is_file():
                continue
            if path.name in known_names:
                continue
            if not is_valid_attachment_name(path.name):
                # Not a file this module created; leave it alone.
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    except OSError:
        return removed
    return removed
