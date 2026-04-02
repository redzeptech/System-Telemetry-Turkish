"""GPU metrikleri (adaptör veya WMI üzerinden)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.telemetry_schema import (
    COMPONENT_GPU,
    METRIC_LOAD,
    METRIC_MEMORY_USAGE,
    METRIC_TEMPERATURE,
    SOURCE_LIBRE_HW,
    STATUS_NORMAL,
    make_telemetry_row,
    with_common_timestamp,
)
from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class GpuCollector:
    """GPU verilerini LHM/WMI adaptörleriyle toplar."""

    def __init__(self, lhm_adapter: Optional[Any] = None) -> None:
        self._lhm = lhm_adapter
        self._logger = get_logger(f"{__name__}.GpuCollector")

    def collect(self, *, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """Şema uyumlu satırlar (GPU başına load, sıcaklık, bellek)."""
        try:
            ts = timestamp or format_iso(utc_now())
            rows: List[Dict[str, Any]] = []
            if self._lhm is not None and hasattr(self._lhm, "get_gpu_metrics"):
                snapshots = list(self._lhm.get_gpu_metrics())
            else:
                self._logger.debug("GPU adaptörü yok; boş liste")
                snapshots = []

            for idx, s in enumerate(snapshots):
                base_sensor = f"gpu_{idx}"
                name = getattr(s, "name", base_sensor)
                if s.load_percent is not None:
                    rows.append(
                        make_telemetry_row(
                            component=COMPONENT_GPU,
                            sensor=str(name),
                            metric=METRIC_LOAD,
                            value=float(s.load_percent),
                            unit="%",
                            source=SOURCE_LIBRE_HW,
                            status=STATUS_NORMAL,
                            timestamp=ts,
                        )
                    )
                if s.temperature_celsius is not None:
                    rows.append(
                        make_telemetry_row(
                            component=COMPONENT_GPU,
                            sensor=str(name),
                            metric=METRIC_TEMPERATURE,
                            value=float(s.temperature_celsius),
                            unit="C",
                            source=SOURCE_LIBRE_HW,
                            status=STATUS_NORMAL,
                            timestamp=ts,
                        )
                    )
                if s.memory_used_mb is not None and s.memory_total_mb:
                    try:
                        pct = 100.0 * float(s.memory_used_mb) / float(s.memory_total_mb)
                    except (TypeError, ZeroDivisionError):
                        pct = 0.0
                    rows.append(
                        make_telemetry_row(
                            component=COMPONENT_GPU,
                            sensor=f"{name}_vram",
                            metric=METRIC_MEMORY_USAGE,
                            value=float(pct),
                            unit="%",
                            source=SOURCE_LIBRE_HW,
                            status=STATUS_NORMAL,
                            timestamp=ts,
                        )
                    )
            return with_common_timestamp(rows, ts)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("GPU toplama hatası: %s", exc)
            raise
