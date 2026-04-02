"""Analiz bulgularından ve telemetriden zenginleştirilmiş alarm kayıtları."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.telemetry_schema import (
    COMPONENT_CPU,
    COMPONENT_FAN,
    METRIC_DISK_USAGE,
    METRIC_FAN_SPEED,
    METRIC_LOAD,
    METRIC_MEMORY_USAGE,
    METRIC_TEMPERATURE,
)
from core.threshold_helpers import get_metric_config
from storage.models import TelemetryData
from utils.helpers import load_yaml
from utils.logger import get_logger
from utils.time_utils import utc_now

from core.alerts.severity import Severity


def alarm_severity_to_int(severity_str: str) -> int:
    """Rapor/DB için string severity → sayı (düşük → yüksek)."""
    return {
        "low": 10,
        "medium": 20,
        "high": 30,
        "critical": 40,
    }.get(str(severity_str).lower(), 20)


@dataclass
class Alert:
    """Eski uyumluluk: basit uyarı kaydı (Severity enum)."""

    title: str
    severity: Severity
    source: str
    details: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


class AlertEngine:
    """
    Alarm motoru: TelemetryData / telemetri satırlarından JSON alarm kaydı üretir.

    Kritik sıcaklık senaryolarında ``details`` içine aynı döngüdeki ``fan_speed`` ve
    ``load`` değerleri eklenir (fan arızası vs. yetersiz soğutma ayrımı için).
    """

    def __init__(self, thresholds_path: Optional[Path] = None) -> None:
        self._logger = get_logger(f"{__name__}.AlertEngine")
        self._thresholds_path = thresholds_path
        self._thresholds: Dict[str, Any] = {}
        if thresholds_path is not None:
            try:
                self._thresholds = load_yaml(thresholds_path)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Eşikler yüklenemedi: %s", exc)

    def reload_thresholds(self, path: Path) -> None:
        """Eşik dosyasını yeniden yükler."""
        self._thresholds_path = path
        self._thresholds = load_yaml(path)

    def map_status_to_alarm_severity(self, status: str) -> str:
        """
        thresholds.yaml → ``alert_severity.mapping`` ile low/medium/high/critical.

        Varsayılan: warning→medium, critical→critical, normal→low.
        """
        try:
            s = status.lower()
            mapping = self._thresholds.get("alert_severity", {}).get("mapping", {})
            if s in ("ok", "normal"):
                return str(mapping.get("normal", "low"))
            if s == "warning":
                return str(mapping.get("warning", "medium"))
            if s == "critical":
                return str(mapping.get("critical", "critical"))
            return str(mapping.get("default", "medium"))
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("Severity eşlemesi: %s", exc)
            return "medium"

    def _maybe_upgrade_warning_to_high(self, row: Dict[str, Any]) -> str:
        """Sıcaklık uyarısında kritik eşiğe yakınsa 'high' döner."""
        base = self.map_status_to_alarm_severity(str(row.get("status", "")))
        if str(row.get("status", "")).lower() != "warning":
            return base
        if str(row.get("metric", "")).lower() != METRIC_TEMPERATURE:
            return base
        ratio = float(
            self._thresholds.get("alert_severity", {}).get(
                "temperature_warning_to_high_ratio",
                0.85,
            ),
        )
        comp = str(row.get("component", "")).lower()
        cfg = get_metric_config(self._thresholds, comp, METRIC_TEMPERATURE)
        if not cfg:
            return base
        try:
            warn = float(cfg.get("warning", 80))
            crit = float(cfg.get("critical", 95))
            val = float(row.get("value", 0.0))
        except (TypeError, ValueError):
            return base
        if crit <= warn:
            return base
        threshold_line = warn + ratio * (crit - warn)
        if val >= threshold_line:
            return str(
                self._thresholds.get("alert_severity", {})
                .get("mapping", {})
                .get("warning_high", "high"),
            )
        return base

    @staticmethod
    def _telemetry_data_from_row(row: Dict[str, Any]) -> TelemetryData:
        """Telemetri satırı sözlüğünden TelemetryData."""
        return TelemetryData.from_dict(row)

    @staticmethod
    def _extract_fan_speed_rpm(readings: List[Dict[str, Any]]) -> Optional[float]:
        """Aynı döngüdeki fan okumalarından ilk geçerli RPM."""
        for r in readings:
            if (
                str(r.get("component", "")).lower() == COMPONENT_FAN
                and str(r.get("metric", "")).lower() == METRIC_FAN_SPEED
            ):
                try:
                    return float(r["value"])
                except (KeyError, TypeError, ValueError):
                    continue
        for r in readings:
            if str(r.get("metric", "")).lower() == METRIC_FAN_SPEED:
                try:
                    return float(r["value"])
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _extract_cpu_load_percent(readings: List[Dict[str, Any]]) -> Optional[float]:
        """cpu_total yük yüzdesi."""
        for r in readings:
            if (
                str(r.get("component", "")).lower() == COMPONENT_CPU
                and str(r.get("metric", "")).lower() == METRIC_LOAD
                and str(r.get("sensor", "")) == "cpu_total"
            ):
                try:
                    return float(r["value"])
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _build_details(
        self,
        primary: Dict[str, Any],
        all_readings: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Birincil satıra göre details; ``status == critical`` ise fan_speed ve load eklenir.
        """
        details: Dict[str, Any] = {}
        metric = str(primary.get("metric", "")).lower()
        try:
            val = float(primary.get("value", 0.0))
        except (TypeError, ValueError):
            val = 0.0

        if metric == METRIC_TEMPERATURE:
            details["temperature"] = round(val, 1)
        elif metric == METRIC_LOAD:
            details["load"] = round(val, 1)
        elif metric == METRIC_FAN_SPEED:
            details["fan_speed"] = round(val, 0)
        else:
            details[metric] = round(val, 4)

        status = str(primary.get("status", "")).lower()
        if status == "critical":
            fs = self._extract_fan_speed_rpm(all_readings)
            if fs is not None and "fan_speed" not in details:
                details["fan_speed"] = round(fs, 0)
            ld = self._extract_cpu_load_percent(all_readings)
            if ld is not None and "load" not in details:
                details["load"] = round(ld, 1)

        return details

    @staticmethod
    def _display_component(comp: str) -> str:
        """Rapor için bileşen adı (örn. cpu -> CPU)."""
        m = {
            "cpu": "CPU",
            "gpu": "GPU",
            "memory": "Memory",
            "disk": "Disk",
            "fan": "Fan",
            "motherboard": "Motherboard",
        }
        return m.get(comp.lower(), comp.upper())

    def _yaml_alarm_meta(
        self,
        comp: str,
        metric: str,
        status: str,
    ) -> Optional[Tuple[str, str, str]]:
        """thresholds.yaml içindeki code / recommendation / title alanları."""
        cfg = get_metric_config(self._thresholds, comp, metric)
        if not cfg:
            return None
        st = status.lower()
        if st == "critical":
            code = cfg.get("code")
            rec = cfg.get("recommendation")
            title = cfg.get("title") or cfg.get("title_critical")
        elif st == "warning":
            code = cfg.get("code_warning")
            rec = cfg.get("recommendation_warning")
            title = cfg.get("title_warning")
        else:
            return None
        if not code and not rec:
            return None
        code = str(code or f"{comp.upper()}_{metric.upper()}_{st.upper()}")
        rec = str(rec or "İlgili eşikleri kontrol edin.")
        if not title:
            title = code.replace("_", " ").title()
        return code, title, rec

    def _alarm_code_title_recommendation(
        self,
        td: TelemetryData,
    ) -> Tuple[str, str, str]:
        """component/metric/status için kod, başlık, öneri (önce YAML, sonra şablon)."""
        comp = td.component.lower()
        metric = td.metric.lower()
        status = td.status.lower()

        y = self._yaml_alarm_meta(comp, metric, status)
        if y is not None:
            return y

        templates: Dict[Tuple[str, str, str], Tuple[str, str, str]] = {
            ("cpu", METRIC_TEMPERATURE, "critical"): (
                "CPU_TEMP_CRITICAL",
                "CPU sıcaklığı kritik seviyeye ulaştı",
                "Soğutucu montajı ve termal macun kontrol edilmeli.",
            ),
            ("cpu", METRIC_TEMPERATURE, "warning"): (
                "CPU_TEMP_WARNING",
                "CPU sıcaklığı yükseldi",
                "Havalandırma ve yükü kontrol edin; termal macun ömrü değerlendirilmeli.",
            ),
            ("gpu", METRIC_TEMPERATURE, "critical"): (
                "GPU_TEMP_CRITICAL",
                "GPU sıcaklığı kritik seviyede",
                "GPU soğutucusu ve kasa hava akışı kontrol edilmeli.",
            ),
            ("gpu", METRIC_TEMPERATURE, "warning"): (
                "GPU_TEMP_WARNING",
                "GPU sıcaklığı yükseldi",
                "Yük ve fan eğrilerini kontrol edin.",
            ),
            ("motherboard", METRIC_TEMPERATURE, "critical"): (
                "MB_TEMP_CRITICAL",
                "Anakart sıcaklığı kritik",
                "Kasa fanları ve kablo yönetimini kontrol edin.",
            ),
            ("cpu", METRIC_LOAD, "critical"): (
                "CPU_LOAD_CRITICAL",
                "CPU kullanımı kritik seviyede",
                "Yükü azaltın veya süreçleri gözden geçirin.",
            ),
            ("cpu", METRIC_LOAD, "warning"): (
                "CPU_LOAD_WARNING",
                "CPU kullanımı yüksek",
                "Kaynak tüketen uygulamaları kontrol edin.",
            ),
            ("memory", METRIC_MEMORY_USAGE, "critical"): (
                "MEMORY_USAGE_CRITICAL",
                "Bellek kullanımı kritik",
                "RAM veya sayfa dosyası ayarlarını gözden geçirin.",
            ),
            ("disk", METRIC_DISK_USAGE, "critical"): (
                "DISK_USAGE_CRITICAL",
                "Disk doluluğu kritik",
                "Alan açın veya depolamayı genişletin.",
            ),
            ("fan", METRIC_FAN_SPEED, "critical"): (
                "FAN_SPEED_CRITICAL",
                "Fan hızı kritik düşük",
                "Fan bağlantısı ve sensörü kontrol edin.",
            ),
        }

        key = (comp, metric, status)
        if key in templates:
            return templates[key]

        code = f"{comp.upper()}_{metric.upper()}_{status.upper()}"
        title = f"{td.component} — {metric} ({status})"
        rec = "İlgili donanım ve eşikleri kontrol edin."
        return code, title, rec

    def build_alarm_record_from_telemetry_data(
        self,
        td: TelemetryData,
        all_readings: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """``TelemetryData`` girdisi ile aynı JSON alarm kaydı."""
        return self.build_alarm_record_json(td.to_dict(), all_readings)

    def build_alarm_record_json(
        self,
        primary_row: Dict[str, Any],
        all_readings: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Örnek alarm JSON formatı: timestamp, severity, code, title, component,
        details (sıcaklık + kritikte fan_speed & load), recommendation.
        """
        try:
            td = self._telemetry_data_from_row(primary_row)
            code, title, recommendation = self._alarm_code_title_recommendation(td)
            severity_str = self._maybe_upgrade_warning_to_high(primary_row)
            details = self._build_details(primary_row, all_readings)

            comp_key = str(primary_row.get("component", ""))
            return {
                "timestamp": str(primary_row.get("timestamp", "")),
                "severity": severity_str,
                "code": code,
                "title": title,
                "component": self._display_component(comp_key),
                "details": details,
                "recommendation": recommendation,
            }
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Alarm kaydı oluşturma hatası: %s", exc)
            raise

    def build_enriched_alarm_records(
        self,
        triggered_rows: List[Dict[str, Any]],
        all_readings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Birden fazla tetiklenen satır için alarm JSON listesi."""
        out: List[Dict[str, Any]] = []
        for row in triggered_rows:
            try:
                if str(row.get("status", "")).lower() in ("ok", "normal"):
                    continue
                out.append(self.build_alarm_record_json(row, all_readings))
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Satır atlandı: %s — %s", row, exc)
        return out

    def from_findings(
        self,
        findings: List[Dict[str, Any]],
        source: str = "analyzer",
    ) -> List[Alert]:
        """Genel bulgu dict listesinden eski Alert nesneleri (geriye uyumluluk)."""
        alerts: List[Alert] = []
        try:
            for f in findings:
                level = str(f.get("level", "ok"))
                if level in ("ok", "normal"):
                    continue
                sev = Severity.from_level_string(level)
                title = str(f.get("message", f.get("metric", "bulgu")))
                alerts.append(
                    Alert(
                        title=title,
                        severity=sev,
                        source=source,
                        details=dict(f),
                    )
                )
            return alerts
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Uyarı oluşturma hatası: %s", exc)
            raise

    def deduplicate(self, alerts: List[Alert], key_fn: Optional[Any] = None) -> List[Alert]:
        """Basit tekrar kaldırma (başlığa göre)."""
        try:
            seen: set[str] = set()
            out: List[Alert] = []
            for a in alerts:
                k = key_fn(a) if callable(key_fn) else a.title
                if k in seen:
                    continue
                seen.add(str(k))
                out.append(a)
            return out
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Deduplicate hatası: %s", exc)
            raise
