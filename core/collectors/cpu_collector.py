"""CPU kullanımı ve frekans toplama."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import psutil

from core.telemetry_schema import (
    COMPONENT_CPU,
    METRIC_CLOCK,
    METRIC_CORE_COUNT,
    METRIC_LOAD,
    METRIC_TEMPERATURE,
    SOURCE_PSUTIL,
    STATUS_NORMAL,
    make_telemetry_row,
    with_common_timestamp,
)
from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class CpuCollector:
    """psutil ile CPU metriklerini toplar; çıktı birleşik telemetri şemasıdır."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.CpuCollector")

    @staticmethod
    def _optional_cpu_temperature_row(ts: str) -> Optional[Dict[str, Any]]:
        """Linux vb.: ``psutil.sensors_temperatures`` ile tek CPU sıcaklık satırı."""
        try:
            fn = getattr(psutil, "sensors_temperatures", None)
            if not callable(fn):
                return None
            temps = fn()
        except (AttributeError, NotImplementedError, OSError, RuntimeError):
            return None
        if not temps:
            return None
        best: Optional[tuple[float, str]] = None
        for chip, entries in temps.items():
            chip_l = str(chip).lower()
            for e in entries:
                if e.current is None:
                    continue
                lab = str(e.label or chip).lower()
                if any(
                    k in lab or k in chip_l
                    for k in ("cpu", "core", "package", "tdie", "tctl", "k10temp")
                ):
                    cur = float(e.current)
                    name = f"{chip}_{e.label or 't'}"
                    if best is None or cur > best[0]:
                        best = (cur, name)
        if best is None:
            for chip, entries in temps.items():
                if not entries:
                    continue
                e0 = entries[0]
                if e0.current is None:
                    continue
                best = (float(e0.current), f"{chip}_{e0.label or 't'}")
                break
        if best is None:
            return None
        val, sensor = best
        return make_telemetry_row(
            component=COMPONENT_CPU,
            sensor=sensor[:48],
            metric=METRIC_TEMPERATURE,
            value=val,
            unit="C",
            source=SOURCE_PSUTIL,
            status=STATUS_NORMAL,
            timestamp=ts,
        )

    def collect(
        self,
        interval: Optional[float] = None,
        *,
        timestamp: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Şema uyumlu telemetri satırları (timestamp tek döngüde ortak olabilir)."""
        try:
            usage = float(psutil.cpu_percent(interval=interval))
            per_cpu = [float(x) for x in psutil.cpu_percent(percpu=True, interval=None)]
            freq = psutil.cpu_freq()
            freq_mhz = float(freq.current) if freq else None
            cores = int(psutil.cpu_count(logical=True) or 0)

            ts = timestamp or format_iso(utc_now())
            rows: List[Dict[str, Any]] = [
                make_telemetry_row(
                    component=COMPONENT_CPU,
                    sensor="cpu_total",
                    metric=METRIC_LOAD,
                    value=usage,
                    unit="%",
                    source=SOURCE_PSUTIL,
                    status=STATUS_NORMAL,
                    timestamp=ts,
                )
            ]
            for i, pct in enumerate(per_cpu):
                rows.append(
                    make_telemetry_row(
                        component=COMPONENT_CPU,
                        sensor=f"core_{i}",
                        metric=METRIC_LOAD,
                        value=pct,
                        unit="%",
                        source=SOURCE_PSUTIL,
                        status=STATUS_NORMAL,
                        timestamp=ts,
                    )
                )
            if freq_mhz is not None:
                rows.append(
                    make_telemetry_row(
                        component=COMPONENT_CPU,
                        sensor="cpu_frequency",
                        metric=METRIC_CLOCK,
                        value=freq_mhz,
                        unit="MHz",
                        source=SOURCE_PSUTIL,
                        status=STATUS_NORMAL,
                        timestamp=ts,
                    )
                )
            rows.append(
                make_telemetry_row(
                    component=COMPONENT_CPU,
                    sensor="logical_cores",
                    metric=METRIC_CORE_COUNT,
                    value=float(cores),
                    unit="count",
                    source=SOURCE_PSUTIL,
                    status=STATUS_NORMAL,
                    timestamp=ts,
                )
            )
            temp_row = self._optional_cpu_temperature_row(ts)
            if temp_row is not None:
                rows.append(temp_row)
            return with_common_timestamp(rows, ts)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("CPU toplama hatası: %s", exc)
            raise
