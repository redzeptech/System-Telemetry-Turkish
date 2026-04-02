"""Katman C: Çoklu sensör korelasyon / çapraz kontrol analizi."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.telemetry_schema import (
    COMPONENT_CPU,
    COMPONENT_FAN,
    METRIC_FAN_SPEED,
    METRIC_LOAD,
    METRIC_TEMPERATURE,
)
from utils.helpers import load_yaml
from utils.logger import get_logger


@dataclass
class CorrelationFinding:
    """Çapraz kontrol bulgusu."""

    code: str
    message: str
    severity: str  # low | medium | high
    details: Dict[str, Any] = field(default_factory=dict)


class CorrelationAnalyzer:
    """
    Örnek kurallar (yük, sıcaklık, fan, trend birlikte):

    - Düşük CPU yükü + yüksek sıcaklık → zayıf termal temas / pompa şüphesi
    - Yüksek fan + yükselen sıcaklık eğilimi → soğutma kapasitesi yetersiz
    """

    def __init__(self, thresholds_path: Optional[Path] = None) -> None:
        self._logger = get_logger(f"{__name__}.CorrelationAnalyzer")
        self._cfg: Dict[str, Any] = {}
        if thresholds_path is not None:
            try:
                full = load_yaml(thresholds_path)
                self._cfg = full.get("correlation", {}) or {}
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Korelasyon eşikleri yüklenemedi: %s", exc)

    def _num(self, key: str, default: float) -> float:
        try:
            return float(self._cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def analyze(self, context: Dict[str, Any]) -> List[CorrelationFinding]:
        """
        context anahtarları (opsiyonel, eksikse kural atlanır):

        - cpu_load: float (%)
        - cpu_temp: float (°C)
        - fan_speed_percent: float | None (0–100, varsa)
        - fan_rpm: float | None
        - cpu_temp_trend: str (rising | stable | falling | unknown)
        """
        findings: List[CorrelationFinding] = []
        try:
            cpu_load = context.get("cpu_load")
            cpu_temp = context.get("cpu_temp")
            trend = str(context.get("cpu_temp_trend", "unknown")).lower()
            fan_pct = context.get("fan_speed_percent")
            fan_rpm = context.get("fan_rpm")

            low_load = self._num("cpu_low_load_max", 20.0)
            high_temp = self._num("cpu_temp_high", 85.0)
            fan_high_pct = self._num("fan_high_percent", 90.0)
            fan_high_rpm = self._num("fan_high_rpm_min", 2500.0)

            if (
                cpu_load is not None
                and cpu_temp is not None
                and float(cpu_load) < low_load
                and float(cpu_temp) > high_temp
            ):
                findings.append(
                    CorrelationFinding(
                        code="THERMAL_CONTACT_OR_PUMP_SUSPECT",
                        message="Zayıf termal temas / pompa arızası şüphesi",
                        severity="high",
                        details={
                            "cpu_load": float(cpu_load),
                            "cpu_temp": float(cpu_temp),
                            "rule": f"load < {low_load} and temp > {high_temp}",
                        },
                    )
                )

            fan_high = False
            if fan_pct is not None:
                fan_high = float(fan_pct) > fan_high_pct
            elif fan_rpm is not None:
                fan_high = float(fan_rpm) >= fan_high_rpm

            if fan_high and trend == "rising":
                findings.append(
                    CorrelationFinding(
                        code="INSUFFICIENT_COOLING_CAPACITY",
                        message="Soğutma kapasitesi yetersiz (yüksek fan, sıcaklık yükseliyor)",
                        severity="high",
                        details={
                            "fan_speed_percent": fan_pct,
                            "fan_rpm": fan_rpm,
                            "cpu_temp_trend": trend,
                        },
                    )
                )

            return findings
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Korelasyon analizi hatası: %s", exc)
            raise

    @staticmethod
    def build_context_from_readings(readings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Telemetri satır listesinden korelasyon bağlamı çıkarır."""
        ctx: Dict[str, Any] = {}
        fan_rpms: List[float] = []
        for r in readings:
            comp = str(r.get("component", "")).lower()
            metric = str(r.get("metric", "")).lower()
            try:
                val = float(r.get("value", 0.0))
            except (TypeError, ValueError):
                continue
            if comp == COMPONENT_CPU and metric == METRIC_LOAD and str(r.get("sensor")) == "cpu_total":
                ctx["cpu_load"] = val
            if comp == COMPONENT_CPU and metric == METRIC_TEMPERATURE:
                ctx["cpu_temp"] = val
            if comp == COMPONENT_FAN and metric == METRIC_FAN_SPEED:
                fan_rpms.append(val)
        if fan_rpms:
            ctx["fan_rpm"] = max(fan_rpms)
        return ctx
