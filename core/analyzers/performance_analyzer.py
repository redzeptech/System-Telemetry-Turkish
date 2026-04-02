"""CPU/RAM performans eşik analizi + Katman B: sıcaklık için rolling window / trend."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from utils.helpers import load_yaml
from utils.logger import get_logger


@dataclass
class PerformanceFinding:
    """Performans bulgusu."""

    metric: str
    value: float
    level: str
    message: str = ""


@dataclass
class _TimeSample:
    """Rolling window içinde tek örnek."""

    at: datetime
    value: float


class RollingWindow:
    """
    Bellekte son ``window_seconds`` saniyelik veriyi tutar (deque).

    Zaman damgası ISO 8601 veya datetime ile uyumludur; eski örnekler
    otomatik düşürülür.
    """

    def __init__(
        self,
        window_seconds: float = 300.0,
        *,
        max_points: int = 2000,
    ) -> None:
        self._window_seconds = float(window_seconds)
        self._deque: Deque[_TimeSample] = deque(maxlen=max_points)
        self._logger = get_logger(f"{__name__}.RollingWindow")

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def clear(self) -> None:
        """Pencereyi boşaltır."""
        self._deque.clear()

    @staticmethod
    def _parse_ts(ts: str) -> datetime:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def push(self, ts_iso: str, value: float, *, now: Optional[datetime] = None) -> None:
        """Yeni örnek ekler; pencere dışı örnekleri silir."""
        try:
            at = self._parse_ts(ts_iso)
            self._deque.append(_TimeSample(at=at, value=float(value)))
            self._evict(now=now)
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("RollingWindow.push atlandı: %s", exc)

    def _evict(self, *, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        if not self._deque:
            return
        limit = self._window_seconds
        while self._deque:
            age = (now - self._deque[0].at).total_seconds()
            if age > limit:
                self._deque.popleft()
            else:
                break

    def pairs(self) -> List[Tuple[float, float]]:
        """(saniye_ofset, değer) — ilk örnek t=0."""
        self._evict()
        if len(self._deque) < 2:
            return []
        t0 = self._deque[0].at
        out: List[Tuple[float, float]] = []
        for s in self._deque:
            sec = (s.at - t0).total_seconds()
            out.append((sec, s.value))
        return out

    def values(self) -> List[float]:
        self._evict()
        return [s.value for s in self._deque]


def _linear_slope_c_per_sec(pairs: List[Tuple[float, float]]) -> float:
    """En küçük kareler eğimi: °C / saniye (x: saniye, y: sıcaklık)."""
    if len(pairs) < 2:
        return 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    if abs(den) < 1e-18:
        return 0.0
    return num / den


class PerformanceAnalyzer:
    """
    Katman A: CPU/RAM kullanım eşikleri (thresholds.yaml → usage).

    Katman B: ``_cpu_temp_window`` ile son 5 dakikalık CPU sıcaklığı; eğim ile
    ``trend`` ``rising`` | ``stable`` | ``falling`` | ``unknown``.
    """

    def __init__(
        self,
        thresholds_path: Optional[Path] = None,
        *,
        trend_window_seconds: float = 300.0,
        rising_slope_min_c_per_s: float = 0.00015,
    ) -> None:
        self._logger = get_logger(f"{__name__}.PerformanceAnalyzer")
        self._thresholds: Dict[str, Any] = {}
        if thresholds_path is not None:
            try:
                self._thresholds = load_yaml(thresholds_path)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Eşikler yüklenemedi: %s", exc)

        self._cpu_temp_window = RollingWindow(trend_window_seconds)
        self._rising_slope_min = float(rising_slope_min_c_per_s)

    def analyze(
        self,
        cpu_percent: float,
        memory_percent: float,
    ) -> List[PerformanceFinding]:
        """CPU ve RAM kullanımını eşiklerle karşılaştırır (Katman A)."""
        findings: List[PerformanceFinding] = []
        try:
            u = self._thresholds.get("usage", {})
            cpu_cfg = u.get("cpu_percent", {})
            mem_cfg = u.get("memory_percent", {})
            findings.extend(
                self._check_metric("cpu", cpu_percent, cpu_cfg),
            )
            findings.extend(
                self._check_metric("memory", memory_percent, mem_cfg),
            )
            return findings
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Performans analiz hatası: %s", exc)
            raise

    def record_cpu_temperature_sample(self, ts_iso: str, value_celsius: float) -> None:
        """Katman B: CPU sıcaklık örneğini pencereye ekler."""
        self._cpu_temp_window.push(ts_iso, value_celsius)

    def cpu_temperature_trend(self) -> str:
        """
        Son penceredeki örneklere göre eğilim.

        Dönüş: ``rising`` | ``stable`` | ``falling`` | ``unknown``
        """
        try:
            pairs = self._cpu_temp_window.pairs()
            if len(pairs) < 3:
                return "unknown"
            slope = _linear_slope_c_per_sec(pairs)
            if slope > self._rising_slope_min:
                return "rising"
            if slope < -self._rising_slope_min:
                return "falling"
            return "stable"
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("Trend hesaplanamadı: %s", exc)
            return "unknown"

    def trend_snapshot(self) -> Dict[str, Any]:
        """Rapor ve korelasyon için özet."""
        pairs = self._cpu_temp_window.pairs()
        slope = _linear_slope_c_per_sec(pairs) if len(pairs) >= 2 else 0.0
        return {
            "window_seconds": self._cpu_temp_window.window_seconds,
            "sample_count": len(pairs),
            "trend": self.cpu_temperature_trend(),
            "slope_c_per_sec": round(slope, 8),
        }

    def _check_metric(
        self,
        name: str,
        value: float,
        cfg: Dict[str, Any],
    ) -> List[PerformanceFinding]:
        warn = float(cfg.get("warning", 85))
        crit = float(cfg.get("critical", 95))
        if value >= crit:
            level = "critical"
        elif value >= warn:
            level = "warning"
        else:
            level = "normal"
        return [
            PerformanceFinding(
                metric=name,
                value=value,
                level=level,
                message=f"{name} kullanımı %{value:.1f}",
            )
        ]
