"""Sıcaklık verilerini eşiklerle karşılaştırır; yük–sıcaklık–fan korelasyonu."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.analyzers.analysis_context import AnalysisDataContext
from core.telemetry_schema import (
    COMPONENT_CPU,
    COMPONENT_FAN,
    METRIC_FAN_SPEED,
    METRIC_LOAD,
    METRIC_TEMPERATURE,
    STATUS_NORMAL,
    STATUS_WARNING,
)
from core.threshold_helpers import get_metric_config
from utils.helpers import load_yaml
from utils.logger import get_logger


@dataclass
class ThermalFinding:
    """Tek bir sıcaklık bulgusu."""

    component: str
    value_celsius: float
    level: str  # ok | warning | critical
    message: str = ""


@dataclass
class CoolingIssueResult:
    """Düşük yük + yükselen sıcaklık (trend) — soğutma sorunu şüphesi."""

    active: bool
    message: str = ""
    code: str = "COOLING_ISSUE"
    details: Dict[str, Any] = field(default_factory=dict)


class ThermalAnalyzer:
    """thresholds.yaml içindeki sıcaklık limitlerini kullanır."""

    def __init__(self, thresholds_path: Optional[Path] = None) -> None:
        self._logger = get_logger(f"{__name__}.ThermalAnalyzer")
        self._thresholds: Dict[str, Any] = {}
        if thresholds_path is not None:
            try:
                self._thresholds = load_yaml(thresholds_path)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Eşikler yüklenemedi: %s", exc)

    def analyze(
        self,
        readings: Dict[str, Dict[str, float]],
    ) -> List[ThermalFinding]:
        """
        readings: örn. {"cpu": {"celsius": 72.0}, "gpu": {"celsius": 65.0}}
        """
        findings: List[ThermalFinding] = []
        try:
            for key, data in readings.items():
                c = float(data.get("celsius", 0.0))
                sub = get_metric_config(self._thresholds, str(key), METRIC_TEMPERATURE)
                if not sub:
                    continue
                warn = float(sub.get("warning", 80))
                crit = float(sub.get("critical", 95))
                if c >= crit:
                    level = "critical"
                elif c >= warn:
                    level = "warning"
                else:
                    level = "normal"
                findings.append(
                    ThermalFinding(
                        component=key,
                        value_celsius=c,
                        level=level,
                        message=f"{key} sıcaklığı {c:.1f}°C",
                    )
                )
            return findings
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Termal analiz hatası: %s", exc)
            raise

    def load_thresholds(self, path: Path) -> None:
        """Eşik dosyasını yeniden yükler."""
        try:
            self._thresholds = load_yaml(path)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Eşik yükleme hatası: %s", exc)
            raise


class ThermalCorrelationAnalyzer:
    """
    ``load``, ``temperature`` ve ``fan_speed`` ilişkisini kontrol eder.

    Depodan gelen sıcaklık geçmişi + anlık ölçümlerle: yük düşükken sıcaklık
    net biçimde artıyorsa **Cooling Issue** üretir (``config/scoring_rules.yaml``).
    """

    def __init__(self, scoring_rules: Optional[Dict[str, Any]] = None) -> None:
        self._logger = get_logger(f"{__name__}.ThermalCorrelationAnalyzer")
        self._rules: Dict[str, Any] = dict(scoring_rules or {})
        ci = self._rules.get("cooling_issue") or {}
        self._max_load = float(ci.get("max_load_percent", 35.0))
        self._min_delta = float(ci.get("min_temp_delta_c", 2.0))
        self._min_points = int(ci.get("min_history_points", 2))
        self._require_fan = bool(ci.get("require_fan_not_proportional", False))

    def analyze_cooling_issue(
        self,
        data_context: AnalysisDataContext,
        current_readings: List[Dict[str, Any]],
    ) -> CoolingIssueResult:
        """
        CPU yükü düşük + sıcaklık serisi (geçmiş + anlık) yükseliyorsa uyarı.

        Seri: depodaki ``cpu|temperature`` satırlarının değerleri + bu döngünün
        güncel CPU Package sıcaklığı (varsa).
        """
        try:
            cpu_load = self._pick_cpu_load(current_readings)
            cpu_temp_now = self._pick_cpu_temp(current_readings)
            hist = data_context.history_series(COMPONENT_CPU, METRIC_TEMPERATURE)

            temps: List[float] = []
            for row in hist:
                try:
                    temps.append(float(row.get("value", 0.0)))
                except (TypeError, ValueError):
                    continue

            if cpu_temp_now is not None:
                temps.append(float(cpu_temp_now))

            if len(temps) < max(2, self._min_points):
                return CoolingIssueResult(
                    active=False,
                    message="Yetersiz sıcaklık geçmişi (trend için daha fazla döngü gerekir).",
                    details={"points": len(temps)},
                )

            delta = temps[-1] - temps[0]
            low_load = cpu_load is not None and float(cpu_load) < self._max_load
            rising = delta >= self._min_delta

            fan_ok = True
            if self._require_fan:
                fan_now = self._max_fan_rpm(current_readings)
                hist_fan = data_context.history_series(COMPONENT_FAN, METRIC_FAN_SPEED)
                prev_fan = self._max_value_from_rows(hist_fan)
                if fan_now is not None and prev_fan is not None:
                    # Fan RPM düşük veya artmıyor ama sıcaklık artıyor
                    fan_ok = float(fan_now) <= float(prev_fan) * 1.05
                else:
                    fan_ok = True

            if low_load and rising and fan_ok:
                return CoolingIssueResult(
                    active=True,
                    message=(
                        f"Düşük CPU yükü (~{float(cpu_load):.1f}%) altında sıcaklık "
                        f"serisi +{delta:.1f}°C artmış; soğutma / termal arayüz sorunu olabilir."
                    ),
                    code="COOLING_ISSUE",
                    details={
                        "cpu_load_percent": cpu_load,
                        "temp_series_first_c": temps[0],
                        "temp_series_last_c": temps[-1],
                        "delta_c": delta,
                        "max_load_threshold": self._max_load,
                        "min_delta_c": self._min_delta,
                    },
                )

            return CoolingIssueResult(
                active=False,
                message="Cooling issue kriterleri tetiklenmedi.",
                details={
                    "cpu_load_percent": cpu_load,
                    "delta_c": delta,
                    "low_load": low_load,
                    "rising": rising,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Thermal korelasyon hatası: %s", exc)
            raise

    @staticmethod
    def apply_cooling_issue_to_readings(
        readings: List[Dict[str, Any]],
        result: CoolingIssueResult,
    ) -> List[Dict[str, Any]]:
        """CPU sıcaklık satırlarına ``analysis_flags`` ve gerekirse ``status`` yazar."""
        if not result.active:
            return readings
        out: List[Dict[str, Any]] = []
        for r in readings:
            row = dict(r)
            comp = str(row.get("component", "")).lower()
            metric = str(row.get("metric", "")).lower()
            if comp == COMPONENT_CPU and metric == METRIC_TEMPERATURE:
                flags = list(row.get("analysis_flags") or [])
                if "cooling_issue" not in flags:
                    flags.append("cooling_issue")
                row["analysis_flags"] = flags
                if str(row.get("status", STATUS_NORMAL)) == STATUS_NORMAL:
                    row["status"] = STATUS_WARNING
            out.append(row)
        return out

    @staticmethod
    def _pick_cpu_load(readings: List[Dict[str, Any]]) -> Optional[float]:
        for r in readings:
            if (
                str(r.get("component", "")).lower() == COMPONENT_CPU
                and str(r.get("metric", "")).lower() == METRIC_LOAD
                and str(r.get("sensor", "")).lower() == "cpu_total"
            ):
                try:
                    return float(r.get("value", 0.0))
                except (TypeError, ValueError):
                    return None
        for r in readings:
            if (
                str(r.get("component", "")).lower() == COMPONENT_CPU
                and str(r.get("metric", "")).lower() == METRIC_LOAD
            ):
                try:
                    return float(r.get("value", 0.0))
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _pick_cpu_temp(readings: List[Dict[str, Any]]) -> Optional[float]:
        for r in readings:
            if (
                str(r.get("component", "")).lower() == COMPONENT_CPU
                and str(r.get("metric", "")).lower() == METRIC_TEMPERATURE
            ):
                try:
                    return float(r.get("value", 0.0))
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _max_fan_rpm(readings: List[Dict[str, Any]]) -> Optional[float]:
        best: Optional[float] = None
        for r in readings:
            if (
                str(r.get("component", "")).lower() == COMPONENT_FAN
                and str(r.get("metric", "")).lower() == METRIC_FAN_SPEED
            ):
                try:
                    v = float(r.get("value", 0.0))
                except (TypeError, ValueError):
                    continue
                best = v if best is None else max(best, v)
        return best

    @staticmethod
    def _max_value_from_rows(rows: List[Dict[str, Any]]) -> Optional[float]:
        best: Optional[float] = None
        for r in rows:
            try:
                v = float(r.get("value", 0.0))
            except (TypeError, ValueError):
                continue
            best = v if best is None else max(best, v)
        return best
