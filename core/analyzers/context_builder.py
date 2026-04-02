"""Depo + anlık telemetriden :class:`AnalysisDataContext` üretir."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.analyzers.analysis_context import AnalysisDataContext, history_key
from storage.repository import TelemetryRepository


# Trend / korelasyon için varsayılan (component, metric) çiftleri
DEFAULT_HISTORY_QUERIES: Tuple[Tuple[str, str], ...] = (
    ("cpu", "temperature"),
    ("cpu", "load"),
    ("fan", "fan_speed"),
    ("gpu", "temperature"),
    ("gpu", "load"),
)


def build_analysis_context(
    repo: TelemetryRepository,
    current_readings: List[Dict[str, Any]],
    *,
    history_limit: int = 10,
    queries: Tuple[Tuple[str, str], ...] = DEFAULT_HISTORY_QUERIES,
) -> AnalysisDataContext:
    """
    ``storage/repository`` üzerinden her (bileşen, metrik) için son
    ``history_limit`` satırı okur (eskiden yeniye sıralı).
    """
    history: Dict[str, List[Dict[str, Any]]] = {}
    for comp, metric in queries:
        rows = repo.recent_rows_for_component_metric(comp, metric, history_limit)
        history[history_key(comp, metric)] = rows
    return AnalysisDataContext(
        current_readings=list(current_readings),
        history_by_component_metric=history,
    )
