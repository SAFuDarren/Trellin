"""Render database-backed command results through the same JSON encoder."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID


def json_default(value):
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")
