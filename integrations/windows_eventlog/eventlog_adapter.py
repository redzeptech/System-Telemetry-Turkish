"""Windows Event Log okuma (win32evtlog veya PowerShell)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

from utils.logger import get_logger


@dataclass
class EventLogRecord:
    """Basit olay kaydı."""

    source: str
    message: str
    time_generated: Optional[datetime] = None
    event_id: Optional[int] = None


class EventLogAdapter:
    """Sistem günlüğünden son olayları okur (iskelet)."""

    def __init__(self, log_name: str = "System") -> None:
        self._log_name = log_name
        self._logger = get_logger(f"{__name__}.EventLogAdapter")

    def read_recent(self, max_events: int = 50) -> List[EventLogRecord]:
        """Son N olayı döndürür."""
        try:
            try:
                import win32evtlog  # type: ignore[import-untyped]
                import win32evtlogutil  # type: ignore[import-untyped]
            except ImportError:
                self._logger.debug("pywin32 yüklü değil; boş liste")
                return []

            hand = win32evtlog.OpenEventLog(None, self._log_name)
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            out: List[EventLogRecord] = []
            for ev in events[:max_events]:
                try:
                    msg = win32evtlogutil.SafeFormatMessage(ev, self._log_name)
                except Exception:  # noqa: BLE001
                    msg = str(ev)
                out.append(
                    EventLogRecord(
                        source=str(ev.SourceName),
                        message=msg,
                        time_generated=ev.TimeGenerated,
                        event_id=int(ev.EventID) if ev.EventID else None,
                    )
                )
            win32evtlog.CloseEventLog(hand)
            return out
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Olay günlüğü okunamadı: %s", exc)
            return []
