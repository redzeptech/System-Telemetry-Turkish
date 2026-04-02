"""Telemetri özet raporu birleştirme."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from utils.time_utils import format_iso, utc_now


class ReportBuilder:
    """Toplanan veri ve analizleri tek rapor yapısında birleştirir."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.ReportBuilder")

    def build(
        self,
        collectors_data: Dict[str, Any],
        analysis: Dict[str, Any],
        health_score: Optional[Dict[str, Any]] = None,
        alerts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Standart rapor sözlüğü oluşturur."""
        try:
            ts = format_iso(utc_now())
            report: Dict[str, Any] = {
                "generated_at": ts,
                "collectors": collectors_data,
                "analysis": analysis,
            }
            if health_score is not None:
                report["health"] = health_score
            if alerts is not None:
                report["alerts"] = alerts
            return report
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Rapor oluşturma hatası: %s", exc)
            raise

    def merge_section(self, base: Dict[str, Any], key: str, value: Any) -> Dict[str, Any]:
        """Mevcut rapora bölüm ekler."""
        try:
            out = dict(base)
            out[key] = value
            return out
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Bölüm birleştirme hatası: %s", exc)
            raise
