"""Birleşik telemetri JSON şeması ve standart metrik/component sabitleri."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.time_utils import format_iso, utc_now

# Metrik adları (sistem genelinde sabit)
METRIC_TEMPERATURE = "temperature"
METRIC_LOAD = "load"
METRIC_FAN_SPEED = "fan_speed"
METRIC_CLOCK = "clock"
METRIC_MEMORY_USAGE = "memory_usage"
METRIC_SWAP_USAGE = "swap_usage"
METRIC_DISK_USAGE = "disk_usage"
METRIC_VOLTAGE = "voltage"
METRIC_CORE_COUNT = "core_count"

# Bileşen adları (küçük harf)
COMPONENT_CPU = "cpu"
COMPONENT_GPU = "gpu"
COMPONENT_MEMORY = "memory"
COMPONENT_DISK = "disk"
COMPONENT_FAN = "fan"
COMPONENT_MOTHERBOARD = "motherboard"

# Kaynak
SOURCE_PSUTIL = "psutil"
SOURCE_LIBRE_HW = "LibreHardwareMonitor"

# Durum
STATUS_NORMAL = "normal"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"

# Analiz bayrakları (satır bazlı, isteğe bağlı)
ANALYSIS_FLAG_COOLING_ISSUE = "cooling_issue"

TELEMETRY_KEYS: tuple[str, ...] = (
    "timestamp",
    "component",
    "sensor",
    "metric",
    "value",
    "unit",
    "status",
    "source",
)

# Standart paket v2.1+: zorunlu alanlar + isteğe bağlı genişletme
OPTIONAL_TELEMETRY_KEYS: tuple[str, ...] = (
    "health_score",
    "analysis_flags",
)


def make_telemetry_row(
    *,
    component: str,
    sensor: str,
    metric: str,
    value: float,
    unit: str,
    source: str,
    status: str = STATUS_NORMAL,
    timestamp: Optional[str] = None,
    health_score: Optional[float] = None,
    analysis_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Şema ile uyumlu tek telemetri sözlüğü üretir."""
    ts = timestamp or format_iso(utc_now())
    row: Dict[str, Any] = {
        "timestamp": ts,
        "component": component,
        "sensor": sensor,
        "metric": metric,
        "value": float(value),
        "unit": unit,
        "status": status,
        "source": source,
    }
    if health_score is not None:
        row["health_score"] = float(health_score)
    if analysis_flags:
        row["analysis_flags"] = list(analysis_flags)
    return row


def validate_telemetry_row(row: Dict[str, Any]) -> bool:
    """Zorunlu anahtarların varlığını kontrol eder."""
    try:
        return all(k in row for k in TELEMETRY_KEYS)
    except Exception:  # noqa: BLE001
        return False


def enrich_readings_with_component_health(
    readings: List[Dict[str, Any]],
    component_scores: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Her satıra bileşenin 0–100 sağlık puanını ``health_score`` olarak yazar."""
    out: List[Dict[str, Any]] = []
    for r in readings:
        d = dict(r)
        comp = str(d.get("component", "")).lower().strip()
        if comp and comp in component_scores:
            d["health_score"] = float(component_scores[comp])
        out.append(d)
    return out


def with_common_timestamp(rows: List[Dict[str, Any]], ts: Optional[str] = None) -> List[Dict[str, Any]]:
    """Tüm satırlara aynı ISO zaman damgasını yazar."""
    stamp = ts or format_iso(utc_now())
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["timestamp"] = stamp
        out.append(d)
    return out
