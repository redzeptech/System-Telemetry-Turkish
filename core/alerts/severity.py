"""Uyarı önem dereceleri."""

from __future__ import annotations

from enum import IntEnum


class Severity(IntEnum):
    """Önem sırası (düşükten yükseğe)."""

    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40

    @classmethod
    def from_level_string(cls, level: str) -> "Severity":
        """'warning', 'critical' gibi dizgilerden enum."""
        try:
            m = {
                "ok": cls.INFO,
                "normal": cls.INFO,
                "info": cls.INFO,
                "warning": cls.WARNING,
                "error": cls.ERROR,
                "critical": cls.CRITICAL,
            }
            return m.get(level.lower(), cls.INFO)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Geçersiz seviye: {level}") from exc
