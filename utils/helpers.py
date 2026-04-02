"""Genel yardımcı fonksiyonlar."""

from __future__ import annotations

import numbers
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from utils.time_utils import format_iso, utc_now


def load_yaml(path: Path) -> Dict[str, Any]:
    """YAML dosyasını güvenli şekilde yükler."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML ayrıştırma hatası: {path}") from exc
    except OSError as exc:
        raise OSError(f"Dosya okunamadı: {path}") from exc


def clamp(value: float, low: float, high: float) -> float:
    """Değeri [low, high] aralığına sıkıştırır."""
    try:
        return max(low, min(high, value))
    except TypeError as exc:
        raise TypeError("clamp için sayısal değerler gerekli") from exc


def safe_get(mapping: Dict[str, Any], key: str, default: Optional[Any] = None) -> Any:
    """Sözlükten güvenli okuma."""
    try:
        return mapping.get(key, default)
    except AttributeError as exc:
        raise TypeError("mapping bir dict olmalı") from exc


def _normalize_value(value: Any) -> Any:
    """Skaler ve iç içe yapıları telemetri için tutarlı tipe çevirir."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_normalize_value(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in value.items()}
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)


def normalize_telemetry_bundle(
    readings: list[Dict[str, Any]],
    *,
    schema_version: str = "2.1",
) -> Dict[str, Any]:
    """
    Toplayıcıların ürettiği şema uyumlu satırları tek pakette birleştirir.

    ``schema_version`` 2.1+: satırlarda isteğe bağlı ``health_score`` ve
    ``analysis_flags`` (ör. ``cooling_issue``) bulunabilir.
    """
    try:
        return {
            "schema_version": schema_version,
            "collected_at": format_iso(utc_now()),
            "readings": _normalize_value(readings),
        }
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Telemetri paketi normalizasyonu başarısız") from exc


def normalize_telemetry_payload(
    collectors_raw: Dict[str, Any],
    *,
    schema_version: str = "1.0",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Toplayıcı çıktılarını tek şemaya oturtur (analizör ve depolama için).

    Dönen yapı: schema_version, collected_at, collectors{...}, isteğe bağlı extra.
    """
    try:
        collected_at = format_iso(utc_now())
        collectors: Dict[str, Any] = {}
        for name, payload in collectors_raw.items():
            if isinstance(payload, dict):
                collectors[str(name)] = _normalize_value(payload)
            else:
                collectors[str(name)] = _normalize_value(payload)
        out: Dict[str, Any] = {
            "schema_version": schema_version,
            "collected_at": collected_at,
            "collectors": collectors,
        }
        if extra:
            out["extra"] = _normalize_value(extra)
        return out
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Telemetri normalizasyonu başarısız") from exc
