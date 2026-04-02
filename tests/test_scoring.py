"""Sağlık puanı testleri."""

from __future__ import annotations

from core.scoring.health_score import HealthScoreCalculator


def test_health_score_all_ok() -> None:
    h = HealthScoreCalculator()
    r = h.compute(
        thermal_levels=["normal"],
        disk_levels=["normal"],
        performance_levels=["normal"],
    )
    assert r.score == 100.0
    assert "thermal" in r.component_scores
    assert isinstance(r.reasons, list)


def test_health_score_mixed() -> None:
    h = HealthScoreCalculator()
    r = h.compute(
        thermal_levels=["warning"],
        disk_levels=["critical"],
        performance_levels=["normal"],
    )
    assert 0.0 <= r.score < 100.0
    assert len(r.reasons) >= 1


def test_health_score_from_readings_weighted() -> None:
    """İki bileşen, ayar ağırlıklarına göre genel skor."""
    settings = {
        "health_scoring": {
            "weights": {
                "cpu": 0.6,
                "gpu": 0.4,
            },
        },
    }
    h = HealthScoreCalculator(settings=settings)
    readings = [
        {
            "timestamp": "t",
            "component": "cpu",
            "sensor": "x",
            "metric": "load",
            "value": 10.0,
            "unit": "%",
            "status": "normal",
            "source": "psutil",
        },
        {
            "timestamp": "t",
            "component": "gpu",
            "sensor": "g",
            "metric": "load",
            "value": 50.0,
            "unit": "%",
            "status": "critical",
            "source": "psutil",
        },
    ]
    r = h.compute_from_readings(readings)
    assert r.component_scores["cpu"] == 100.0
    assert r.component_scores["gpu"] < 100.0
    assert 0.0 <= r.score < 100.0
    assert len(r.reasons) >= 1
    assert "weights_effective" in r.factors
