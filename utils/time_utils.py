"""Zaman damgası ve aralık yardımcıları."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """UTC zamanını döndürür."""
    try:
        return datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("UTC zamanı alınamadı") from exc


def format_iso(dt: Optional[datetime] = None) -> str:
    """ISO 8601 formatında zaman damgası."""
    try:
        t = dt or utc_now()
        return t.isoformat()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Zaman formatlanamadı") from exc
