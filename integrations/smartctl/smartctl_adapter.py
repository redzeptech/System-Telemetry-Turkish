"""
Smartmontools ``smartctl`` ile disk SMART verisi okuma.

``smartctl`` harici bir çalıştırılabilir dosyadır (Windows’ta genelde ``smartctl.exe``);
Smartmontools kurulmalı ve PATH’e eklenmeli veya ``binary`` parametresi tam yol olmalıdır.
Python bu veriyi alt süreç olarak çalıştırarak alır; dahili disk tanısı yapmaz.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

from utils.logger import get_logger


class SmartctlAdapter:
    """
    ``smartctl -j`` JSON çıktısını ayrıştırır.

    İkili yol: sistem PATH’inde ``smartctl`` / ``smartctl.exe`` veya yapılandırmadan
    gelen tam yol (bkz. ``config/settings.yaml`` → ``integrations.smartctl.binary``).
    """

    def __init__(self, binary: str = "smartctl") -> None:
        self._binary = binary
        self._logger = get_logger(f"{__name__}.SmartctlAdapter")

    def run_smartctl_json(self, device: str) -> Dict[str, Any]:
        """Belirtilen disk için `smartctl -j -a` çıktısını döndürür."""
        try:
            cmd = [self._binary, "-j", "-a", device]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 0:
                self._logger.warning(
                    "smartctl hata kodu %s: %s",
                    proc.returncode,
                    proc.stderr[:500],
                )
                return {}
            return json.loads(proc.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as exc:
            self._logger.warning("smartctl çalıştırılamadı: %s", exc)
            return {}
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("smartctl beklenmeyen hata: %s", exc)
            raise

    def list_devices(self) -> List[str]:
        """Basit disk listesi (Windows'ta genelde PhysicalDrive N)."""
        try:
            # İskelet: gerçek listeleme ortama göre genişletilir
            return []
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Disk listesi hatası: %s", exc)
            raise
