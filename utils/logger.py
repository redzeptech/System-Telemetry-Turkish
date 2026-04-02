"""Standart uygulama loglama yapılandırması."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logger(
    name: str = "system_telemetry",
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """
    Uygulama genelinde kullanılacak logger oluşturur.

    Args:
        name: Logger adı.
        level: Log seviyesi (örn. logging.DEBUG).
        log_file: İsteğe bağlı dosya yolu; verilirse dosyaya da yazar.
        format_string: Özel format; None ise varsayılan kullanılır.

    Returns:
        Yapılandırılmış logging.Logger örneği.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    fmt = format_string or "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    try:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        logger.addHandler(console)

        if log_file is not None:
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
                file_handler.setLevel(level)
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except OSError as exc:
                logger.warning("Log dosyası açılamadı: %s", exc)
    except Exception as exc:  # noqa: BLE001 — logger kurulumu için geniş yakalama
        logging.basicConfig(level=level, format=fmt)
        logging.getLogger(name).error("Logger kurulum hatası: %s", exc)

    return logger


def get_logger(name: str = "system_telemetry") -> logging.Logger:
    """Mevcut veya yeni bir alt logger döndürür."""
    return logging.getLogger(name)
