"""Analizörler için veri bağlamı: anlık ölçümler + depodan trend geçmişi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


def history_key(component: str, metric: str) -> str:
    """Depo sorgularında kullanılan tek anahtar: ``cpu|temperature``."""
    return f"{str(component).strip().lower()}|{str(metric).strip().lower()}"


@dataclass
class AnalysisDataContext:
    """
    Bu döngünün toplanan satırları ve ``TelemetryRepository`` üzerinden
    okunan son N kayıt (bileşen/metrik başına), trend ve korelasyon için.
    """

    current_readings: List[Dict[str, Any]]
    history_by_component_metric: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=dict,
    )

    def history_series(self, component: str, metric: str) -> List[Dict[str, Any]]:
        return list(self.history_by_component_metric.get(history_key(component, metric), []))
