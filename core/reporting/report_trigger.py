"""Gunluk PDF rapor orkestrasyonu: repo + ``prepare_chart_data`` + ``FpdfDailyReportBuilder``."""

from __future__ import annotations

from pathlib import Path

from core.reporting.chart_data import prepare_chart_data
from core.reporting.export_pdf import FpdfDailyReportBuilder, get_latest_health_from_repository
from storage.db import Database
from storage.repository import TelemetryRepository


def run_gunluk_ozet_pdf(db_path: Path, output_path: Path) -> Path:
    """
    Veritabanından son telemetri ve olayları alır; özet + CPU sıcaklık grafiği + olay günlüğü PDF üretir.

    Akış: ``get_recent_telemetry`` → ``prepare_chart_data`` → ``create_thermal_chart({...})``
    → ``add_chart``; sağlık ``telemetry_snapshots`` üzerinden.
    """
    db = Database(db_path)
    repo = TelemetryRepository(db)
    raw_telemetry = repo.get_recent_telemetry(50)
    incidents = repo.get_daily_incidents()
    health_status = get_latest_health_from_repository(repo)

    builder = FpdfDailyReportBuilder(repo)
    builder.start_document()
    builder.add_hardware_inventory_section()
    builder.add_summary_section(health_status, new_page=False)
    times, temps = prepare_chart_data(raw_telemetry)
    thermal_img = builder.create_thermal_chart({"timestamps": times, "temps": temps})
    if thermal_img:
        builder.add_chart(thermal_img, title="CPU Sicaklik Grafigi", new_page=True)
    builder.add_incident_log(incidents, new_page=True)
    return builder.build_report(output_path)
