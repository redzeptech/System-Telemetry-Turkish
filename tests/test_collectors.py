"""Toplayıcı birim testleri."""

from __future__ import annotations

from pathlib import Path

from core.collectors.cpu_collector import CpuCollector
from core.collectors.memory_collector import MemoryCollector
from core.telemetry_schema import METRIC_LOAD, METRIC_MEMORY_USAGE


def test_cpu_collector_returns_schema_rows() -> None:
    c = CpuCollector()
    rows = c.collect(interval=None)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    loads = [r for r in rows if r.get("metric") == METRIC_LOAD and r.get("sensor") == "cpu_total"]
    assert len(loads) == 1
    assert 0.0 <= float(loads[0]["value"]) <= 100.0


def test_memory_collector_returns_schema_rows() -> None:
    m = MemoryCollector()
    rows = m.collect()
    assert any(r.get("metric") == METRIC_MEMORY_USAGE for r in rows)


def test_helpers_load_yaml_missing(tmp_path: Path) -> None:
    from utils.helpers import load_yaml

    assert load_yaml(tmp_path / "nope.yaml") == {}


def test_normalize_telemetry_payload() -> None:
    from utils.helpers import normalize_telemetry_payload

    raw = {"cpu": {"usage_percent": 10.5}}
    n = normalize_telemetry_payload(raw)
    assert n["schema_version"] == "1.0"
    assert "collected_at" in n
    assert n["collectors"]["cpu"]["usage_percent"] == 10.5
