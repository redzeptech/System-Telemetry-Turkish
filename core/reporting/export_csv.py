"""CSV dışa aktarma (pandas)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.logger import get_logger


class CsvExporter:
    """Tablo verisini CSV olarak kaydeder."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.CsvExporter")

    def export_records(
        self,
        records: List[Dict[str, Any]],
        path: Path,
        index: bool = False,
    ) -> None:
        """Kayıt listesini CSV dosyasına yazar."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame(records)
            df.to_csv(path, index=index, encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("CSV yazma hatası: %s", exc)
            raise

    def export_dataframe(self, df: pd.DataFrame, path: Path, **kwargs: Any) -> None:
        """DataFrame doğrudan kayıt."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, encoding="utf-8-sig", **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("DataFrame CSV hatası: %s", exc)
            raise
