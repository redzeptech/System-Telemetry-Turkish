"""Uyarı testleri."""

from __future__ import annotations

from pathlib import Path

from core.alerts.alert_engine import AlertEngine
from core.alerts.severity import Severity


def test_alert_engine_from_findings() -> None:
    eng = AlertEngine()
    findings = [
        {"level": "warning", "message": "yüksek CPU", "metric": "cpu"},
        {"level": "ok", "message": "normal"},
    ]
    alerts = eng.from_findings(findings)
    assert len(alerts) == 1
    assert alerts[0].severity == Severity.WARNING


def test_severity_from_string() -> None:
    assert Severity.from_level_string("critical") == Severity.CRITICAL


def test_enriched_alarm_cpu_temp_critical_details() -> None:
    """Kritik CPU sıcaklığında details içinde fan_speed ve load bağlamı."""
    p = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"
    eng = AlertEngine(p)
    readings = [
        {
            "timestamp": "2026-04-02T16:11:05+03:00",
            "component": "cpu",
            "sensor": "cpu_total",
            "metric": "load",
            "value": 91.0,
            "unit": "%",
            "status": "normal",
            "source": "psutil",
        },
        {
            "timestamp": "2026-04-02T16:11:05+03:00",
            "component": "fan",
            "sensor": "chassis",
            "metric": "fan_speed",
            "value": 1180.0,
            "unit": "RPM",
            "status": "normal",
            "source": "LibreHardwareMonitor",
        },
        {
            "timestamp": "2026-04-02T16:11:05+03:00",
            "component": "cpu",
            "sensor": "package",
            "metric": "temperature",
            "value": 96.4,
            "unit": "C",
            "status": "critical",
            "source": "psutil",
        },
    ]
    primary = readings[2]
    alarm = eng.build_alarm_record_json(primary, readings)
    assert alarm["severity"] == "critical"
    assert alarm["code"] == "CPU_TEMP_CRITICAL"
    assert alarm["title"] == "CPU sıcaklığı kritik seviyeye ulaştı"
    assert alarm["component"] == "CPU"
    assert alarm["details"]["temperature"] == 96.4
    assert alarm["details"]["fan_speed"] == 1180.0
    assert alarm["details"]["load"] == 91.0
    assert "recommendation" in alarm
