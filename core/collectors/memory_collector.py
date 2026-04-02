"""Bellek (RAM) kullanım toplama."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import psutil

from core.telemetry_schema import (
    COMPONENT_MEMORY,
    METRIC_MEMORY_USAGE,
    METRIC_SWAP_USAGE,
    SOURCE_PSUTIL,
    STATUS_NORMAL,
    make_telemetry_row,
    with_common_timestamp,
)
from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class MemoryCollector:
    """psutil ile RAM ve swap metriklerini toplar."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.MemoryCollector")

    def collect(self, *, timestamp: Optional[str] = None) -> List[Dict[str, Any]]:
        """Şema uyumlu telemetri satırları."""
        try:
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()
            ts = timestamp or format_iso(utc_now())
            rows: List[Dict[str, Any]] = [
                make_telemetry_row(
                    component=COMPONENT_MEMORY,
                    sensor="virtual_memory",
                    metric=METRIC_MEMORY_USAGE,
                    value=float(vm.percent),
                    unit="%",
                    source=SOURCE_PSUTIL,
                    status=STATUS_NORMAL,
                    timestamp=ts,
                ),
                make_telemetry_row(
                    component=COMPONENT_MEMORY,
                    sensor="swap",
                    metric=METRIC_SWAP_USAGE,
                    value=float(swap.percent),
                    unit="%",
                    source=SOURCE_PSUTIL,
                    status=STATUS_NORMAL,
                    timestamp=ts,
                ),
            ]
            return with_common_timestamp(rows, ts)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Bellek toplama hatası: %s", exc)
            raise
