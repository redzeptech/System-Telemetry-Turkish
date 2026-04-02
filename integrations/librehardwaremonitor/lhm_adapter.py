"""
LibreHardwareMonitor (LHM) köprüsü.

LHM ayrı kurulan bir uygulamadır (PyPI paketi değil). Tipik veri kaynakları:
- Gömülü web sunucusu: belirtilen portta JSON (ör. ``/data.json``); uzaktan erişim LHM
  ayarlarından etkinleştirilir.
- WMI: Sensörler Windows WMI ile de okunabilir; bu modül şu an JSON URL iskeletine
  odaklanır; WMI için ``integrations/wmi/wmi_adapter.py`` kullanılabilir veya bu sınıf
  genişletilebilir.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import urlopen

from core.collectors.types import FanReading, GpuSnapshot, MotherboardReading
from utils.logger import get_logger


class LhmAdapter:
    """
    LHM JSON HTTP uç noktasından veri çeker (web sunucusu + JSON port senaryosu).

    WMI tabanlı okuma bu sınıfta yoksa, ayrı bir WMI yolu veya ``fetch_json`` sonrası
    ham ``dict`` ile ``_cache`` üzerinden ayrıştırma eklenebilir.
    """

    def __init__(self, json_url: Optional[str] = None) -> None:
        self._json_url = json_url
        self._logger = get_logger(f"{__name__}.LhmAdapter")
        self._cache: Dict[str, Any] = {}

    def fetch_json(self) -> Dict[str, Any]:
        """URL'den JSON yükler."""
        if not self._json_url:
            self._logger.debug("LHM URL tanımlı değil")
            return {}
        try:
            with urlopen(self._json_url, timeout=5) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            self._cache = data if isinstance(data, dict) else {}
            return self._cache
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            self._logger.warning("LHM JSON alınamadı: %s", exc)
            return {}
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("LHM beklenmeyen hata: %s", exc)
            return {}

    def get_gpu_metrics(self) -> List[GpuSnapshot]:
        """GPU metrikleri — gerçek ayrıştırma LHM şemasına göre doldurulur."""
        try:
            _ = self.fetch_json()
            return []
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("GPU metrik ayrıştırma: %s", exc)
            raise

    def get_fan_readings(self) -> List[FanReading]:
        """Fan okumaları."""
        try:
            _ = self.fetch_json()
            return []
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Fan okuma: %s", exc)
            raise

    def get_motherboard_sensors(self) -> List[MotherboardReading]:
        """Anakart sensörleri."""
        try:
            _ = self.fetch_json()
            return []
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Anakart sensör: %s", exc)
            raise
