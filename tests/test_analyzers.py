"""Analizör testleri."""

from __future__ import annotations

from pathlib import Path

from core.analyzers.anomaly_detector import AnomalyDetector
from core.analyzers.performance_analyzer import PerformanceAnalyzer
from core.analyzers.telemetry_row_analyzer import TelemetryRowAnalyzer
from core.telemetry_schema import (
    COMPONENT_CPU,
    METRIC_LOAD,
    SOURCE_PSUTIL,
    STATUS_NORMAL,
    make_telemetry_row,
)


def test_performance_analyzer_ok(tmp_path: Path) -> None:
    p = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"
    a = PerformanceAnalyzer(p)
    findings = a.analyze(10.0, 20.0)
    assert len(findings) == 2
    assert all(f.level == "normal" for f in findings)


def test_anomaly_detector() -> None:
    d = AnomalyDetector(z_threshold=2.0)
    hist = [10.0, 10.5, 10.2, 10.1]
    r = d.detect("cpu", hist, 50.0)
    assert r.is_anomaly is True


def test_telemetry_row_analyzer_cpu_load_normal() -> None:
    p = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"
    rows = [
        make_telemetry_row(
            component=COMPONENT_CPU,
            sensor="cpu_total",
            metric=METRIC_LOAD,
            value=12.0,
            unit="%",
            source=SOURCE_PSUTIL,
            status=STATUS_NORMAL,
        )
    ]
    out = TelemetryRowAnalyzer(p).analyze_rows(rows)
    assert out[0]["status"] == STATUS_NORMAL
