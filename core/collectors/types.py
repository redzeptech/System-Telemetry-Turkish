"""LHM ve harici adaptörler için veri sınıfları."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GpuSnapshot:
    """GPU anlık görüntüsü (adaptör çıktısı)."""

    name: str
    load_percent: Optional[float] = None
    temperature_celsius: Optional[float] = None
    memory_used_mb: Optional[float] = None
    memory_total_mb: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FanReading:
    """Tek fan okuması."""

    name: str
    rpm: Optional[float] = None
    percent: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MotherboardReading:
    """Anakart sensör okuması."""

    sensor_type: str
    label: str
    value: Optional[float] = None
    unit: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
