"""Review exact Library revisions without overwriting a research record."""

import re
from uuid import UUID

from research_multiplier.storage.postgres import DatabaseOperationError


class LibraryError(ValueError):
    """A safe error code for a workbench Library revision."""


_ERRORS = {
    "22023": "libraryEdit.invalid",
    "23514": "libraryEdit.stale",
    "P0002": "libraryEdit.missing",
    "42501": "libraryEdit.identity",
    "42883": "libraryEdit.unavailable",
}


def _call(store, statement, *args):
    try:
        rows = store.query("human", statement, *args)
    except DatabaseOperationError as error:
        if error.state in _ERRORS:
            raise LibraryError(_ERRORS[error.state]) from None
        raise
    if rows[0]["result"] is None:
        raise LibraryError("libraryEdit.missing")
    return rows[0]["result"]


def item(store, version_id):
    return _call(store, "SELECT rm.library_revision_item(%s) AS result", version_id)


def _text(value, length):
    if not isinstance(value, str) or not value.strip() or len(value) > length or "\0" in value:
        raise LibraryError("libraryEdit.invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise LibraryError("libraryEdit.invalid") from None
    return value


def _checksum(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise LibraryError("libraryEdit.invalid")
    return value


def _revision(document, *, confirm=False):
    fields = {"id", "sha256", "natural", "lean"}
    if confirm:
        fields |= {"token", "reason"}
    if not isinstance(document, dict) or document.keys() != fields:
        raise LibraryError("libraryEdit.invalid")
    try:
        identifier = UUID(_text(document["id"], 36))
    except ValueError:
        raise LibraryError("libraryEdit.invalid") from None
    return identifier, _checksum(document["sha256"]), _text(document["natural"], 4000), _text(document["lean"], 16000)


def preview(store, document):
    return _call(store, "SELECT rm.library_revision_preview(%s,%s,%s,%s) AS result", *_revision(document))


def confirm(store, document):
    revision = _revision(document, confirm=True)
    token, reason = _checksum(document["token"]), _text(document["reason"], 2000)
    try:
        researcher = store.local_user()
    except ValueError:
        raise LibraryError("libraryEdit.identity") from None
    if researcher is None:
        raise LibraryError("libraryEdit.identity")
    return _call(store, "SELECT rm.confirm_library_revision(%s,%s,%s,%s,%s,%s,%s) AS result",
                 *revision, token, researcher["display_name"], reason)
