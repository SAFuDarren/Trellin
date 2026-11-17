"""Canonical serialization for application-owned JSON and content digests."""

import hashlib
import json


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def terminal_line(value):
    """Render recorded research text as one line without terminal controls."""
    return " ".join("".join(char for char in str(value) if char.isprintable() or char.isspace()).split())
