"""Anakart voltaj/sıcaklık vb. (LHM/WMI adaptör)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.telemetry_schema import (
    COMPONENT_MOTHERBOARD,
    METRIC_TEMPERATURE,
    METRIC_VOLTAGE,
    SOURCE_LIBRE_HW,
    STATUS_NORMAL,
    make_telemetry_row,
    with_common_timestamp,
)
from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class MotherboardCollector:
    """Anakart sensörlerini harici adaptörden toplar."""

    def __init__(self, lhm_adapter: Optional[Any] = None) -> None:
        self._lhm = lhm_adapter
        self._logger = get_logger(f"{__name__}.MotherboardCollector")

    def collect(self, *, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """Sensör tipine göre temperature veya voltage metrikleri."""
        try:
            ts = timestamp or format_iso(utc_now())
            rows: List[Dict[str, Any]] = []
            if self._lhm is not None and hasattr(self._lhm, "get_motherboard_sensors"):
                readings = list(self._lhm.get_motherboard_sensors())
            else:
                self._logger.debug("Anakart adaptörü yok; boş liste")
                readings = []

            for r in readings:
                st = str(getattr(r, "sensor_type", "")).lower()
                label = str(getattr(r, "label", "sensor"))
                val = getattr(r, "value", None)
                if val is None:
                    continue
                unit = str(getattr(r, "unit", "") or "")
                if "temp" in st:
                    metric = METRIC_TEMPERATURE
                    u = "C" if not unit else unit
                elif "volt" in st or "voltage" in st:
                    metric = METRIC_VOLTAGE
                    u = "V" if not unit else unit
                else:
                    metric = METRIC_TEMPERATURE
                    u = unit or ""
                rows.append(
                    make_telemetry_row(
                        component=COMPONENT_MOTHERBOARD,
                        sensor=label,
                        metric=metric,
                        value=float(val),
                        unit=u,
                        source=SOURCE_LIBRE_HW,
                        status=STATUS_NORMAL,
                        timestamp=ts,
                    )
                )
            return with_common_timestamp(rows, ts)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Anakart toplama hatası: %s", exc)
            raise
