"""PDF günlük olay raporu."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core.reporting.export_pdf import (
    PdfExporter,
    _utc_day_bounds,
    build_fpdf_daily_report,
)
from storage.db import Database
from storage.models import AlertRecord
from storage.repository import TelemetryRepository


def _insert_row(
    repo: TelemetryRepository,
    *,
    ts: str,
    component: str,
    sensor: str,
    metric: str,
    value: float,
    unit: str,
) -> None:
    repo.insert_telemetry_row(
        {
            "timestamp": ts,
            "component": component,
            "sensor": sensor,
            "metric": metric,
            "value": value,
            "unit": unit,
            "status": "normal",
            "source": "test",
        },
    )


def test_utc_day_bounds() -> None:
    s, e = _utc_day_bounds(date(2026, 4, 2))
    assert s.startswith("2026-04-02")
    assert e.startswith("2026-04-03")


def test_daily_pdf_created(tmp_path: Path) -> None:
    """Bugün (UTC) + son 24 saat snapshot + kritik alarm → PDF (kırmızı kutu + grafik)."""
    now = datetime.now(timezone.utc)
    day = now.date()
    start_iso, end_iso = _utc_day_bounds(day)
    assert start_iso < end_iso

    db = Database(tmp_path / "t.db")
    repo = TelemetryRepository(db)

    repo.insert_alert(
        AlertRecord(
            created_at=now,
            title="CPU sıcaklığı kritik",
            severity=40,
            payload_json=json.dumps(
                {
                    "code": "CPU_TEMP_CRITICAL",
                    "recommendation": "Acil: yükü düşürün ve soğutmayı kontrol edin.",
                    "component": "CPU",
                    "details": {"temperature_c": 96.5},
                },
                ensure_ascii=False,
            ),
        ),
    )

    for hours_ago, scores in [
        (0, {"cpu": 92.0, "gpu": 90.0, "memory": 85.0}),
        (2, {"cpu": 88.0, "gpu": 88.0, "memory": 80.0}),
    ]:
        ts = now - timedelta(hours=hours_ago)
        repo.insert_snapshot_package(
            {
                "generated_at": ts.isoformat(),
                "health": {
                    "score": 87.0,
                    "component_scores": scores,
                },
            },
        )

    out = tmp_path / "gunluk.pdf"
    PdfExporter().export_daily_incident_report(out, db_path=tmp_path / "t.db", report_date=day)
    assert out.is_file()
    assert out.stat().st_size > 2500


def test_system_telemetry_report_24h(tmp_path: Path) -> None:
    """export_system_telemetry_report: telemetry + incidents + reasons."""
    now = datetime.now(timezone.utc)
    db = Database(tmp_path / "sys.db")
    repo = TelemetryRepository(db)

    _insert_row(
        repo,
        ts=now.isoformat(),
        component="cpu",
        sensor="pkg",
        metric="temperature",
        value=62.0,
        unit="C",
    )
    _insert_row(
        repo,
        ts=(now - timedelta(hours=1)).isoformat(),
        component="memory",
        sensor="virtual",
        metric="memory_usage",
        value=55.0,
        unit="%",
    )
    repo.insert_alert(
        AlertRecord(
            created_at=now,
            title="Test",
            severity=40,
            payload_json=json.dumps({"code": "X", "recommendation": "Y"}),
        ),
    )
    repo.insert_snapshot_package(
        {
            "generated_at": now.isoformat(),
            "health": {
                "score": 90.0,
                "reasons": ["CPU yuksek", "Fan kontrol"],
            },
        },
    )

    out = tmp_path / "system.pdf"
    PdfExporter().export_system_telemetry_report(out, db_path=tmp_path / "sys.db")
    assert out.is_file()
    assert out.stat().st_size > 2000


def test_fpdf_daily_report(tmp_path: Path) -> None:
    """FPDF2 SystemReport + FpdfDailyReportBuilder."""
    now = datetime.now(timezone.utc)
    db = Database(tmp_path / "fpdf.db")
    repo = TelemetryRepository(db)
    repo.insert_telemetry_row(
        {
            "timestamp": now.isoformat(),
            "component": "cpu",
            "sensor": "t",
            "metric": "temperature",
            "value": 55.0,
            "unit": "C",
            "status": "normal",
            "source": "test",
        },
    )
    repo.insert_snapshot_package(
        {"generated_at": now.isoformat(), "health": {"score": 88.0, "reasons": ["OK"]}},
    )
    out = tmp_path / "fpdf_out.pdf"
    build_fpdf_daily_report(out, tmp_path / "fpdf.db")
    assert out.is_file()
    assert out.stat().st_size > 500
