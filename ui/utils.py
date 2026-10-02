"""Funciones de formato compartidas por la interfaz (sin dependencias de Flet)."""
from __future__ import annotations

from datetime import datetime, timezone

MONTHS_ES = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def short_hash(digest: str | None, head: int = 10, tail: int = 6) -> str:
    if not digest:
        return "—"
    if len(digest) <= head + tail + 1:
        return digest
    return f"{digest[:head]}…{digest[-tail:]}"


def event_datetime(value: str | None) -> datetime | None:
    """Acepta ISO-8601 o el consensus_timestamp de Hedera ('1759300000.000000001')."""
    if not value:
        return None
    try:
        if value.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone()
    except (ValueError, OverflowError, OSError):
        return None


def local_date(value: str | None) -> str:
    moment = event_datetime(value)
    if moment is None:
        return value or "—"
    return moment.strftime("%d/%m/%Y %H:%M")


def full_date(value: str | None) -> str | None:
    moment = event_datetime(value)
    return moment.strftime("%d/%m/%Y · %H:%M:%S") if moment else None


def spanish_day(moment: datetime) -> str:
    return f"{moment.day:02d} {MONTHS_ES[moment.month - 1]} {moment.year}"
