"""
Katman A — Threshold (eşik) analizi.

Gelen her telemetri satırını anlık olarak ``thresholds.yaml`` limitleriyle
karşılaştırır; basit eşik mantığı, ``status`` alanını günceller.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.telemetry_schema import (
    COMPONENT_CPU,
    COMPONENT_DISK,
    COMPONENT_FAN,
    COMPONENT_GPU,
    COMPONENT_MEMORY,
    COMPONENT_MOTHERBOARD,
    METRIC_CLOCK,
    METRIC_CORE_COUNT,
    METRIC_DISK_USAGE,
    METRIC_FAN_SPEED,
    METRIC_LOAD,
    METRIC_MEMORY_USAGE,
    METRIC_SWAP_USAGE,
    METRIC_TEMPERATURE,
    METRIC_VOLTAGE,
    STATUS_CRITICAL,
    STATUS_NORMAL,
    STATUS_WARNING,
)
from core.threshold_helpers import get_metric_config
from utils.helpers import load_yaml
from utils.logger import get_logger


class TelemetryRowAnalyzer:
    """
    Her telemetri sözlüğünü ``thresholds.yaml`` ile karşılaştırır;
    ``value`` aynı kalır, ``status`` güncellenir.
    """

    def __init__(self, thresholds_path: Optional[Path] = None) -> None:
        self._logger = get_logger(f"{__name__}.TelemetryRowAnalyzer")
        self._thresholds: Dict[str, Any] = {}
        if thresholds_path is not None:
            try:
                self._thresholds = load_yaml(thresholds_path)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Eşikler yüklenemedi: %s", exc)

    def analyze_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Girdi satırlarının kopyası üzerinde status alanını günceller."""
        try:
            out: List[Dict[str, Any]] = []
            for row in rows:
                r = deepcopy(row)
                r["status"] = self._status_for_row(r)
                out.append(r)
            return out
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Satır analizi hatası: %s", exc)
            raise

    def _status_for_row(self, row: Dict[str, Any]) -> str:
        comp = str(row.get("component", "")).lower()
        metric = str(row.get("metric", "")).lower()
        try:
            val = float(row.get("value", 0.0))
        except (TypeError, ValueError):
            return STATUS_NORMAL

        if metric == METRIC_CORE_COUNT or metric == METRIC_CLOCK or metric == METRIC_VOLTAGE:
            return STATUS_NORMAL

        if metric == METRIC_TEMPERATURE:
            return self._temperature_status(comp, val)

        if metric == METRIC_LOAD:
            return self._load_status(comp, val)

        if metric in (METRIC_MEMORY_USAGE, METRIC_SWAP_USAGE):
            return self._memory_like_status(val)

        if metric == METRIC_DISK_USAGE:
            return self._disk_usage_status(val)

        if metric == METRIC_FAN_SPEED:
            return self._fan_status(val)

        return STATUS_NORMAL

    def _temperature_status(self, component: str, value: float) -> str:
        cfg = get_metric_config(self._thresholds, component, METRIC_TEMPERATURE)
        if not cfg:
            return STATUS_NORMAL
        try:
            warn = float(cfg.get("warning", 80))
            crit = float(cfg.get("critical", 95))
        except (TypeError, ValueError):
            return STATUS_NORMAL
        return self._upper_bound_status(value, warn, crit)

    def _load_status(self, component: str, value: float) -> str:
        u = self._thresholds.get("usage", {})
        if component == COMPONENT_CPU:
            sub = u.get("cpu_percent", {})
        elif component == COMPONENT_GPU:
            sub = u.get("cpu_percent", {})
        else:
            return STATUS_NORMAL
        warn = float(sub.get("warning", 85))
        crit = float(sub.get("critical", 95))
        return self._upper_bound_status(value, warn, crit)

    def _memory_like_status(self, value: float) -> str:
        sub = self._thresholds.get("usage", {}).get("memory_percent", {})
        warn = float(sub.get("warning", 85))
        crit = float(sub.get("critical", 95))
        return self._upper_bound_status(value, warn, crit)

    def _disk_usage_status(self, value: float) -> str:
        sub = self._thresholds.get("usage", {}).get("disk_percent", {})
        warn = float(sub.get("warning", 85))
        crit = float(sub.get("critical", 95))
        return self._upper_bound_status(value, warn, crit)

    def _fan_status(self, value: float) -> str:
        sub = self._thresholds.get("fan", {}).get("min_rpm", {})
        warn = float(sub.get("warning", 500))
        crit = float(sub.get("critical", 200))
        return self._lower_rpm_status(value, warn, crit)

    @staticmethod
    def _upper_bound_status(value: float, warn: float, crit: float) -> str:
        """Yüksek değer kötü (sıcaklık, yüzde)."""
        if value >= crit:
            return STATUS_CRITICAL
        if value >= warn:
            return STATUS_WARNING
        return STATUS_NORMAL

    @staticmethod
    def _lower_rpm_status(value: float, warn: float, crit: float) -> str:
        """Düşük RPM kötü; eşikler min_rpm uyarı/kritik."""
        if value <= crit:
            return STATUS_CRITICAL
        if value <= warn:
            return STATUS_WARNING
        return STATUS_NORMAL
