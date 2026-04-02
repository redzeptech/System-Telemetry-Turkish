"""Disk doluluk ve SMART özet analizi."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.helpers import load_yaml
from utils.logger import get_logger


@dataclass
class DiskFinding:
    """Disk bulgusu."""

    mountpoint: str
    used_percent: float
    level: str
    message: str = ""


class DiskAnalyzer:
    """thresholds.yaml disk kullanım eşikleriyle karşılaştırır."""

    def __init__(self, thresholds_path: Optional[Path] = None) -> None:
        self._logger = get_logger(f"{__name__}.DiskAnalyzer")
        self._thresholds: Dict[str, Any] = {}
        if thresholds_path is not None:
            try:
                self._thresholds = load_yaml(thresholds_path)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Eşikler yüklenemedi: %s", exc)

    def analyze_usage(self, partitions: List[Dict[str, Any]]) -> List[DiskFinding]:
        """Her mount için kullanım yüzdesini değerlendirir."""
        findings: List[DiskFinding] = []
        try:
            usage_cfg = self._thresholds.get("usage", {}).get("disk_percent", {})
            warn = float(usage_cfg.get("warning", 85))
            crit = float(usage_cfg.get("critical", 95))
            for p in partitions:
                pct = float(p.get("used_percent", 0.0))
                mp = str(p.get("mountpoint", ""))
                if pct >= crit:
                    level = "critical"
                elif pct >= warn:
                    level = "warning"
                else:
                    level = "normal"
                findings.append(
                    DiskFinding(
                        mountpoint=mp,
                        used_percent=pct,
                        level=level,
                        message=f"{mp} doluluk %{pct:.1f}",
                    )
                )
            return findings
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Disk analiz hatası: %s", exc)
            raise
