"""chart_data.prepare_chart_data testleri."""

from __future__ import annotations

from core.reporting.chart_data import prepare_chart_data


def test_prepare_chart_data_cpu_temperature_chronological() -> None:
    rows = [
        {
            "timestamp": "2026-04-02T14:00:00+00:00",
            "component": "cpu",
            "sensor": "pkg",
            "metric": "temperature",
            "value": 50.0,
        },
        {
            "timestamp": "2026-04-02T14:05:00+00:00",
            "component": "cpu",
            "sensor": "pkg",
            "metric": "temperature",
            "value": 55.0,
        },
        {
            "timestamp": "2026-04-02T14:05:00+00:00",
            "component": "gpu",
            "sensor": "x",
            "metric": "temperature",
            "value": 60.0,
        },
    ]
    times, vals = prepare_chart_data(rows, component="cpu", metric="temperature")
    assert times == ["14:00:00", "14:05:00"]
    assert vals == [50.0, 55.0]


def test_prepare_chart_data_case_insensitive() -> None:
    rows = [
        {
            "timestamp": "2026-01-01T09:30:45Z",
            "component": "CPU",
            "metric": "Temperature",
            "value": 42.0,
        },
    ]
    t, v = prepare_chart_data(rows)
    assert t == ["09:30:45"]
    assert v == [42.0]


def test_prepare_chart_data_empty() -> None:
    assert prepare_chart_data([]) == ([], [])
