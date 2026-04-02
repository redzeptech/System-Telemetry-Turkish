"""Telemetry rollup (saatlik özet + ham silme)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.db import Database
from storage.repository import TelemetryRepository


def test_rollup_moves_old_rows_to_aggregates(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    repo = TelemetryRepository(db)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    repo.insert_telemetry_row(
        {
            "timestamp": old,
            "component": "cpu",
            "sensor": "pkg",
            "metric": "temperature",
            "value": 60.0,
            "unit": "C",
            "status": "normal",
            "source": "test",
        },
    )
    repo.insert_telemetry_row(
        {
            "timestamp": old,
            "component": "cpu",
            "sensor": "pkg",
            "metric": "temperature",
            "value": 62.0,
            "unit": "C",
            "status": "normal",
            "source": "test",
        },
    )
    ins, deleted = repo.rollup_telemetry_older_than_hours(1.0)
    assert ins >= 1
    assert deleted == 2
    conn = db.connection
    n_raw = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    n_agg = conn.execute("SELECT COUNT(*) FROM telemetry_aggregates").fetchone()[0]
    assert n_raw == 0
    assert n_agg >= 1
