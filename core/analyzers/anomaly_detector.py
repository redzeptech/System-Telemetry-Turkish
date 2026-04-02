"""Basit istatistiksel anomali tespiti (iskelet)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, stdev
from typing import List, Optional, Sequence

from utils.logger import get_logger


@dataclass
class AnomalyResult:
    """Anomali tespit sonucu."""

    metric_name: str
    value: float
    is_anomaly: bool
    z_score: Optional[float] = None
    reason: str = ""


class AnomalyDetector:
    """Zaman serisi değerlerinde basit z-score tabanlı anomali."""

    def __init__(self, z_threshold: float = 3.0) -> None:
        self._z_threshold = z_threshold
        self._logger = get_logger(f"{__name__}.AnomalyDetector")

    def detect(
        self,
        metric_name: str,
        history: Sequence[float],
        current: float,
    ) -> AnomalyResult:
        """Geçmişe göre mevcut değerin anomali olup olmadığını döndürür."""
        try:
            if len(history) < 2:
                return AnomalyResult(
                    metric_name=metric_name,
                    value=current,
                    is_anomaly=False,
                    reason="yetersiz geçmiş veri",
                )
            m = mean(history)
            sd = stdev(history)
            if sd == 0:
                z = 0.0
            else:
                z = (current - m) / sd
            is_anom = abs(z) >= self._z_threshold
            return AnomalyResult(
                metric_name=metric_name,
                value=current,
                is_anomaly=is_anom,
                z_score=z,
                reason="z-score eşiği aşıldı" if is_anom else "normal",
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Anomali tespit hatası: %s", exc)
            raise

    def batch_detect(
        self,
        series: List[tuple[str, Sequence[float], float]],
    ) -> List[AnomalyResult]:
        """Birden fazla metrik için toplu tespit."""
        results: List[AnomalyResult] = []
        try:
            for name, hist, cur in series:
                results.append(self.detect(name, hist, cur))
            return results
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Toplu anomali hatası: %s", exc)
            raise
