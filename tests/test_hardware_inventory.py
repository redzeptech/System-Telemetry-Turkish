"""Donanım envanteri toplama."""

from __future__ import annotations

from core.reporting.hardware_inventory import collect_hardware_inventory


def test_collect_hardware_inventory_keys() -> None:
    d = collect_hardware_inventory()
    assert set(d.keys()) == {"cpu", "gpu", "ram", "os"}
    for v in d.values():
        assert isinstance(v, str)
        assert len(v) > 0
