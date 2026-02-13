"""Text formatting rules applied to field values before rendering."""

import re
from datetime import datetime
from typing import Optional


def apply_format(value: str, rule: str, warn_callback=None) -> str:
    """Apply *rule* to *value* and return the formatted string.

    Supported rules (case-insensitive):
        uppercase       — "JOHN DOE"
        lowercase       — "john doe"
        titlecase       — "John Doe"
        DD MMM YYYY     — reformat an ISO-ish date string
        DD/MM/YYYY      — ditto
        YYYY-MM-DD      — ditto
        today           — ignore value, insert today's date
        today:FMT       — today's date with a strftime-style format
        number:N        — format as float with N decimal places
    """
    if not rule:
        return value

    r = rule.strip()
    rl = r.lower()

    # --- case transforms ---
    if rl == "uppercase":
        return value.upper()
    if rl == "lowercase":
        return value.lower()
    if rl == "titlecase":
        return value.title()
    if rl == "sentencecase":
        return value[0].upper() + value[1:].lower() if value else value

    # --- today's date ---
    if rl.startswith("today"):
        fmt = "%d %b %Y"
        if ":" in r:
            fmt = r.split(":", 1)[1].strip()
        return datetime.now().strftime(fmt)

    # --- number formatting ---
    if rl.startswith("number:"):
        try:
            decimals = int(r.split(":", 1)[1])
            return f"{float(value):.{decimals}f}"
        except (ValueError, IndexError):
            _warn(warn_callback, f"Cannot format '{value}' with rule '{rule}'")
            return value

    # --- date reformatting ---
    parsed = _try_parse_date(value)
    if parsed:
        fmt_map = {
            "dd mmm yyyy": "%d %b %Y",
            "dd/mm/yyyy": "%d/%m/%Y",
            "yyyy-mm-dd": "%Y-%m-%d",
            "mm/dd/yyyy": "%m/%d/%Y",
            "mmm dd, yyyy": "%b %d, %Y",
        }
        fmt = fmt_map.get(rl)
        if fmt:
            return parsed.strftime(fmt)

    # Unknown rule — return value unchanged, optionally warn
    _warn(warn_callback, f"Unknown format rule '{rule}' — value unchanged")
    return value


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_DATE_FMTS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
]


def _try_parse_date(value: str) -> Optional[datetime]:
    v = value.strip()
    if not v:
        return None
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _warn(cb, msg: str) -> None:
    if cb:
        cb(msg)


def sanitize_for_pdf(text: str) -> str:
    """Remove or replace characters that could corrupt PDF output."""
    # Remove null bytes
    text = text.replace("\x00", "")
    # Replace common problematic control chars (keep newline, tab)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


def safe_filename(template: str, record: dict[str, str]) -> str:
    """Build a filename from *template* substituting {Column} placeholders,
    then strip illegal filename characters."""
    result = template
    for key, val in record.items():
        result = result.replace("{" + key + "}", val)
    # Remove characters illegal in Windows filenames
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", result)
    # Collapse repeated underscores / spaces
    result = re.sub(r"[_ ]{2,}", "_", result).strip("_. ")
    return result or "certificate"
