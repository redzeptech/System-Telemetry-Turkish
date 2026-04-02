"""Disk kullanım ve bölüm bilgisi toplama."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import psutil

from core.telemetry_schema import (
    COMPONENT_DISK,
    METRIC_DISK_USAGE,
    SOURCE_PSUTIL,
    STATUS_NORMAL,
    make_telemetry_row,
    with_common_timestamp,
)
from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class DiskCollector:
    """psutil ile disk kullanımını toplar."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.DiskCollector")

    def collect(self, *, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """Her mount için disk_usage satırı."""
        try:
            ts = timestamp or format_iso(utc_now())
            rows: List[Dict[str, Any]] = []
            for p in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                    rows.append(
                        make_telemetry_row(
                            component=COMPONENT_DISK,
                            sensor=str(p.mountpoint),
                            metric=METRIC_DISK_USAGE,
                            value=float(usage.percent),
                            unit="%",
                            source=SOURCE_PSUTIL,
                            status=STATUS_NORMAL,
                            timestamp=ts,
                        )
                    )
                except PermissionError:
                    continue
            return with_common_timestamp(rows, ts)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Disk toplama hatası: %s", exc)
            raise
