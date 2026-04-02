"""Veri bağlamı, depo geçmişi ve termal soğutma korelasyonu testleri."""

from __future__ import annotations

from pathlib import Path

from core.analyzers.analysis_context import AnalysisDataContext, history_key
from core.analyzers.context_builder import build_analysis_context
from core.analyzers.thermal_analyzer import ThermalCorrelationAnalyzer
from core.telemetry_schema import (
    COMPONENT_CPU,
    METRIC_LOAD,
    METRIC_TEMPERATURE,
    SOURCE_PSUTIL,
    STATUS_NORMAL,
    make_telemetry_row,
)
from storage.db import Database
from storage.repository import TelemetryRepository


def test_recent_rows_chronological(tmp_path: Path) -> None:
    db = Database(tmp_path / "telemetry.db")
    repo = TelemetryRepository(db)
    for i, v in enumerate([45.0, 46.0, 48.0]):
        repo.insert_telemetry_row(
            {
                "timestamp": f"2026-01-01T00:00:0{i}+00:00",
                "component": "cpu",
                "sensor": "pkg",
                "metric": "temperature",
                "value": v,
                "unit": "°C",
                "status": "normal",
                "source": "test",
            },
        )
    rows = repo.recent_rows_for_component_metric("cpu", "temperature", 10)
    assert [float(r["value"]) for r in rows] == [45.0, 46.0, 48.0]


def test_cooling_issue_triggers() -> None:
    hist = [
        {
            "timestamp": "t0",
            "component": "cpu",
            "sensor": "pkg",
            "metric": "temperature",
            "value": 40.0,
            "unit": "°C",
            "status": "normal",
            "source": "test",
        },
        {
            "timestamp": "t1",
            "component": "cpu",
            "sensor": "pkg",
            "metric": "temperature",
            "value": 50.0,
            "unit": "°C",
            "status": "normal",
            "source": "test",
        },
    ]
    ctx = AnalysisDataContext(
        current_readings=[],
        history_by_component_metric={history_key("cpu", "temperature"): hist},
    )
    current = [
        make_telemetry_row(
            component=COMPONENT_CPU,
            sensor="cpu_total",
            metric=METRIC_LOAD,
            value=12.0,
            unit="%",
            source=SOURCE_PSUTIL,
            status=STATUS_NORMAL,
        ),
        make_telemetry_row(
            component=COMPONENT_CPU,
            sensor="CPU Package",
            metric=METRIC_TEMPERATURE,
            value=58.0,
            unit="°C",
            source=SOURCE_PSUTIL,
            status=STATUS_NORMAL,
        ),
    ]
    rules = {
        "cooling_issue": {
            "max_load_percent": 35.0,
            "min_temp_delta_c": 2.0,
            "min_history_points": 2,
        },
    }
    a = ThermalCorrelationAnalyzer(rules)
    r = a.analyze_cooling_issue(ctx, current)
    assert r.active is True
    assert r.code == "COOLING_ISSUE"


def test_apply_cooling_flags_cpu_temp_row() -> None:
    rows = [
        make_telemetry_row(
            component=COMPONENT_CPU,
            sensor="CPU Package",
            metric=METRIC_TEMPERATURE,
            value=70.0,
            unit="°C",
            source=SOURCE_PSUTIL,
        ),
    ]
    from core.analyzers.thermal_analyzer import CoolingIssueResult

    out = ThermalCorrelationAnalyzer.apply_cooling_issue_to_readings(
        rows,
        CoolingIssueResult(active=True, message="x"),
    )
    assert out[0].get("analysis_flags") == ["cooling_issue"]


def test_build_analysis_context_queries(tmp_path: Path) -> None:
    db = Database(tmp_path / "t2.db")
    repo = TelemetryRepository(db)
    current = [
        make_telemetry_row(
            component=COMPONENT_CPU,
            sensor="cpu_total",
            metric=METRIC_LOAD,
            value=5.0,
            unit="%",
            source=SOURCE_PSUTIL,
        ),
    ]
    ctx = build_analysis_context(repo, current, history_limit=5)
    assert ctx.current_readings == current
    assert isinstance(ctx.history_by_component_metric, dict)
