"""Üç katmanlı analizör bileşen testleri."""

from __future__ import annotations

from pathlib import Path

from core.analyzers.correlation_analyzer import CorrelationAnalyzer
from core.analyzers.performance_analyzer import PerformanceAnalyzer


def test_rolling_window_trend_rising() -> None:
    pa = PerformanceAnalyzer(
        None,
        trend_window_seconds=3600.0,
        rising_slope_min_c_per_s=0.00001,
    )
    for i in range(5):
        pa.record_cpu_temperature_sample(
            f"2026-04-02T16:{i:02d}:00+00:00",
            40.0 + i * 2.0,
        )
    assert pa.cpu_temperature_trend() == "rising"


def test_correlation_low_load_high_temp() -> None:
    p = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"
    c = CorrelationAnalyzer(p)
    findings = c.analyze(
        {
            "cpu_load": 15.0,
            "cpu_temp": 90.0,
            "cpu_temp_trend": "stable",
        }
    )
    assert any(f.code == "THERMAL_CONTACT_OR_PUMP_SUSPECT" for f in findings)


def test_correlation_fan_high_and_rising() -> None:
    p = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"
    c = CorrelationAnalyzer(p)
    findings = c.analyze(
        {
            "cpu_load": 50.0,
            "cpu_temp": 70.0,
            "fan_rpm": 3000.0,
            "cpu_temp_trend": "rising",
        }
    )
    assert any(f.code == "INSUFFICIENT_COOLING_CAPACITY" for f in findings)
