"""Fan hızı toplama (LHM/WMI adaptör)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.telemetry_schema import (
    COMPONENT_FAN,
    METRIC_FAN_SPEED,
    SOURCE_LIBRE_HW,
    STATUS_NORMAL,
    make_telemetry_row,
    with_common_timestamp,
)
from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class FanCollector:
    """Fan sensörlerini harici adaptörden toplar."""

    def __init__(self, lhm_adapter: Optional[Any] = None) -> None:
        self._lhm = lhm_adapter
        self._logger = get_logger(f"{__name__}.FanCollector")

    def collect(self, *, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """fan_speed (RPM) satırları."""
        try:
            ts = timestamp or format_iso(utc_now())
            rows: List[Dict[str, Any]] = []
            if self._lhm is not None and hasattr(self._lhm, "get_fan_readings"):
                readings = list(self._lhm.get_fan_readings())
            else:
                self._logger.debug("Fan adaptörü yok; boş liste")
                readings = []

            for r in readings:
                rpm = getattr(r, "rpm", None)
                if rpm is None:
                    continue
                rows.append(
                    make_telemetry_row(
                        component=COMPONENT_FAN,
                        sensor=str(r.name),
                        metric=METRIC_FAN_SPEED,
                        value=float(rpm),
                        unit="RPM",
                        source=SOURCE_LIBRE_HW,
                        status=STATUS_NORMAL,
                        timestamp=ts,
                    )
                )
            return with_common_timestamp(rows, ts)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Fan toplama hatası: %s", exc)
            raise
