"""Private bytes-in, bounded page-text-out protocol with the bundled PDF helper."""

from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys
import time


MAX_RAW_BYTES = 16 * 1024 * 1024
EXTRACTION_SECONDS = 10
_MAX_PAGES = 128
_ERRORS = {"pdf_invalid", "pdf_locked", "pdf_no_text", "pdf_page_limit", "pdf_text_limit"}


def bundled_extractor():
    """Locate the sibling helper only for the app's own interpreter."""
    executable = Path(sys.executable)
    if (sys.platform != "darwin" or not executable.is_absolute()
            or executable.parent.parts[-4:] != ("Contents", "Resources", "python", "bin")
            or executable.parents[4].suffix != ".app"):
        return None
    try:
        contents = executable.parents[4].resolve(strict=True) / "Contents"
        # The packaged python3 can be a symlink to its versioned sibling.
        if executable.resolve(strict=True).parent != contents / "Resources" / "python" / "bin":
            return None
        helper = contents / "Helpers" / "trellin-pdf-preview"
        if helper.resolve(strict=True) != helper or not helper.is_file() or not os.access(helper, os.X_OK):
            return None
    except (OSError, RuntimeError):
        return None
    return partial(_run_helper, helper)


def _run_helper(helper, raw, budget, timeout):
    return subprocess.run(
        [str(helper), str(budget)], input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=True, timeout=timeout, cwd="/", env={},
    ).stdout


def _unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


def _parse_reply(raw, budget):
    # JSON can escape each input byte as six ASCII bytes, plus up to 128 page records.
    if not isinstance(raw, bytes) or len(raw) > 6 * budget + 8192:
        raise ValueError
    row = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
    if not isinstance(row, dict):
        raise ValueError
    if row.keys() == {"status"} and isinstance(row["status"], str) and row["status"] in _ERRORS:
        return row, 0
    if row.keys() != {"status", "pages", "page_count", "pages_included_through"} or row["status"] != "available":
        raise ValueError
    total, through, pages = row["page_count"], row["pages_included_through"], row["pages"]
    if (type(total) is not int or type(through) is not int or not 1 <= through <= total <= _MAX_PAGES
            or not isinstance(pages, list) or not 1 <= len(pages) <= through):
        raise ValueError
    last, used = 0, 0
    for item in pages:
        if not isinstance(item, dict) or item.keys() != {"page", "text"}:
            raise ValueError
        page, text = item["page"], item["text"]
        if (type(page) is not int or not last < page <= through
                or not isinstance(text, str) or "\x00" in text or not text.strip()):
            raise ValueError
        used += len(text.encode("utf-8"))
        if used > budget:
            raise ValueError
        last = page
    return row, used


def extract(worker, raw, budget, deadline):
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        return {"status": "pdf_timeout"}, 0
    try:
        reply = worker(raw, budget, timeout)
        if time.monotonic() >= deadline:
            return {"status": "pdf_timeout"}, 0
        return _parse_reply(reply, budget)
    except subprocess.TimeoutExpired:
        return {"status": "pdf_timeout"}, 0
    except (OSError, subprocess.CalledProcessError, ValueError, RecursionError):
        return {"status": "pdf_unavailable"}, 0
