"""Tkinter masaüstü ana pencere iskeleti."""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext
from typing import Any, Dict, Optional

from utils.logger import get_logger


class MainWindow:
    """Basit telemetri metin görünümü."""

    def __init__(self, title: str = "Sistem Telemetri") -> None:
        self._logger = get_logger(f"{__name__}.MainWindow")
        self._root: Optional[tk.Tk] = None
        self._title = title
        self._text: Optional[scrolledtext.ScrolledText] = None

    def build(self) -> None:
        """Pencereyi oluşturur."""
        try:
            self._root = tk.Tk()
            self._root.title(self._title)
            self._text = scrolledtext.ScrolledText(self._root, width=80, height=24)
            self._text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        except tk.TclError as exc:
            self._logger.exception("Tk pencere hatası: %s", exc)
            raise

    def set_report_text(self, report: Dict[str, Any]) -> None:
        """Rapor içeriğini gösterir."""
        try:
            if self._text is None:
                return
            self._text.delete("1.0", tk.END)
            self._text.insert(tk.END, str(report))
        except tk.TclError as exc:
            self._logger.error("Metin güncelleme: %s", exc)
            raise

    def run(self) -> None:
        """Ana döngü."""
        try:
            if self._root is None:
                self.build()
            assert self._root is not None
            self._root.mainloop()
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("mainloop hatası: %s", exc)
            raise
