"""JSON dışa aktarma."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from utils.logger import get_logger


class JsonExporter:
    """Raporu JSON dosyasına yazar."""

    def __init__(self, indent: int = 2) -> None:
        self._indent = indent
        self._logger = get_logger(f"{__name__}.JsonExporter")

    def export(self, data: Dict[str, Any], path: Path) -> None:
        """Dict'i UTF-8 JSON olarak kaydeder."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=self._indent)
        except OSError as exc:
            self._logger.exception("JSON yazma hatası: %s", exc)
            raise
        except TypeError as exc:
            self._logger.exception("JSON serileştirme hatası: %s", exc)
            raise

    def to_string(self, data: Dict[str, Any]) -> str:
        """JSON dizgesi döndürür."""
        try:
            return json.dumps(data, ensure_ascii=False, indent=self._indent)
        except TypeError as exc:
            self._logger.error("JSON string hatası: %s", exc)
            raise
