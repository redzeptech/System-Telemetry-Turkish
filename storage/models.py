"""SQLite / JSON ile uyumlu veri modelleri."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from utils.time_utils import utc_now


@dataclass
class TelemetryData:
    """Normalleştirilmiş sensör ölçümü (telemetri satırı)."""

    timestamp: str  # ISO 8601 format
    component: str  # CPU, GPU, Disk, vb.
    sensor: str  # "CPU Package", "Core #1"
    metric: str  # "temperature", "load", "clock"
    value: float
    unit: str  # "C", "%", "MHz"
    status: str  # "normal", "warning", "critical"
    source: str  # "LibreHardwareMonitor", "psutil"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TelemetryData":
        """Telemetri şema sözlüğünden örnek oluşturur."""
        return cls(
            timestamp=str(d["timestamp"]),
            component=str(d["component"]),
            sensor=str(d["sensor"]),
            metric=str(d["metric"]),
            value=float(d["value"]),
            unit=str(d["unit"]),
            status=str(d["status"]),
            source=str(d["source"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        """JSON serileştirme için dict."""
        try:
            return asdict(self)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("TelemetryData dict dönüşümü başarısız") from exc


@dataclass
class TelemetryRecord:
    """Tek bir ölçüm kaydı."""

    id: Optional[int] = None
    created_at: datetime = field(default_factory=utc_now)
    component: str = ""
    metric_name: str = ""
    value: float = 0.0
    unit: str = ""
    raw_json: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """JSON serileştirme için dict."""
        try:
            d = asdict(self)
            d["created_at"] = self.created_at.isoformat()
            return d
        except Exception as exc:  # noqa: BLE001
            raise ValueError("TelemetryRecord dict dönüşümü başarısız") from exc


@dataclass
class AlertRecord:
    """Kalıcı uyarı kaydı."""

    id: Optional[int] = None
    created_at: datetime = field(default_factory=utc_now)
    title: str = ""
    severity: int = 0
    acknowledged: bool = False
    payload_json: str = "{}"

    def to_dict(self) -> Dict[str, Any]:
        try:
            d = asdict(self)
            d["created_at"] = self.created_at.isoformat()
            return d
        except Exception as exc:  # noqa: BLE001
            raise ValueError("AlertRecord dict dönüşümü başarısız") from exc


@dataclass
class HealthSnapshotRecord:
    """Sağlık puanı anlık görüntüsü."""

    id: Optional[int] = None
    created_at: datetime = field(default_factory=utc_now)
    score: float = 0.0
    details_json: str = "{}"
