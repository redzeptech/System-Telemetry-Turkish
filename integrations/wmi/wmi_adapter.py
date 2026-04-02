"""Windows WMI ile sensör ve sistem bilgisi."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logger import get_logger


class WmiAdapter:
    """wmi modülü ile sorgu iskeleti."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.WmiAdapter")
        self._wmi: Optional[Any] = None

    def connect(self) -> None:
        """WMI bağlantısı kurar."""
        try:
            import wmi as wmi_module

            self._wmi = wmi_module.WMI()
        except ImportError as exc:
            self._logger.warning("wmi modülü yüklü değil: %s", exc)
            self._wmi = None
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("WMI bağlantı hatası: %s", exc)
            raise

    def query_temperatures(self) -> List[Dict[str, Any]]:
        """MsaTemperature gibi sınıflar üzerinden sıcaklık (iskelet)."""
        try:
            if self._wmi is None:
                self.connect()
            if self._wmi is None:
                return []
            # Örnek: gerçek sınıf adı donanıma göre değişir
            return []
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("WMI sıcaklık sorgusu: %s", exc)
            raise

    def query_disks(self) -> List[Dict[str, Any]]:
        """Win32_DiskDrive özeti."""
        try:
            if self._wmi is None:
                self.connect()
            if self._wmi is None:
                return []
            out: List[Dict[str, Any]] = []
            for d in self._wmi.Win32_DiskDrive():  # type: ignore[attr-defined]
                out.append(
                    {
                        "model": getattr(d, "Model", ""),
                        "size": getattr(d, "Size", None),
                    }
                )
            return out
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("WMI disk sorgusu: %s", exc)
            raise
