"""Gunluk PDF orkestrasyonu (``report_trigger``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.reporting.report_trigger import run_gunluk_ozet_pdf
from storage.db import Database
from storage.repository import TelemetryRepository


def test_run_gunluk_ozet_pdf_writes_file(tmp_path: Path) -> None:
    """``get_recent_telemetry`` + ``prepare_chart_data`` + FPDF cikti dosyasi."""
    now = datetime.now(timezone.utc)
    db = Database(tmp_path / "orch.db")
    repo = TelemetryRepository(db)
    for i in range(5):
        ts = (now - timedelta(minutes=i)).isoformat()
        repo.insert_telemetry_row(
            {
                "timestamp": ts,
                "component": "cpu",
                "sensor": "pkg",
                "metric": "temperature",
                "value": 48.0 + float(i) * 0.5,
                "unit": "C",
                "status": "normal",
                "source": "test",
            },
        )
    repo.insert_snapshot_package(
        {
            "generated_at": now.isoformat(),
            "health": {"score": 81.0, "reasons": ["test"]},
        },
    )
    out = tmp_path / "gunluk_ozet.pdf"
    path = run_gunluk_ozet_pdf(tmp_path / "orch.db", out)
    assert path == out
    assert out.is_file()
    assert out.stat().st_size > 800
