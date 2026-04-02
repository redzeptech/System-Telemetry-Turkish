"""Telemetry ve uyarı kayıtları için depo."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from storage.db import Database
from storage.models import AlertRecord, HealthSnapshotRecord
from utils.logger import get_logger


class TelemetryRepository:
    """SQLite üzerinde CRUD işlemleri."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._logger = get_logger(f"{__name__}.TelemetryRepository")

    def insert_telemetry_row(self, row: Dict[str, Any]) -> int:
        """Birleşik şema telemetri satırı ekler."""
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                INSERT INTO telemetry (
                    timestamp, component, sensor, metric, value, unit, status, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["timestamp"]),
                    str(row["component"]),
                    str(row["sensor"]),
                    str(row["metric"]),
                    float(row["value"]),
                    str(row["unit"]),
                    str(row["status"]),
                    str(row["source"]),
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
            self._logger.exception("insert_telemetry_row: %s", exc)
            raise

    def insert_telemetry_rows(self, rows: List[Dict[str, Any]]) -> List[int]:
        """Birden fazla telemetri satırı ekler."""
        ids: List[int] = []
        for r in rows:
            ids.append(self.insert_telemetry_row(r))
        return ids

    def insert_alert(self, record: AlertRecord) -> int:
        """Uyarı ekler."""
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                INSERT INTO alerts (created_at, title, severity, acknowledged, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.created_at.isoformat(),
                    record.title,
                    record.severity,
                    1 if record.acknowledged else 0,
                    record.payload_json,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            self._logger.exception("insert_alert: %s", exc)
            raise

    def insert_health(self, record: HealthSnapshotRecord) -> int:
        """Sağlık anlık görüntüsü ekler."""
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                INSERT INTO health_snapshots (created_at, score, details_json)
                VALUES (?, ?, ?)
                """,
                (
                    record.created_at.isoformat(),
                    record.score,
                    record.details_json,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        except sqlite3.Error as exc:
            self._logger.exception("insert_health: %s", exc)
            raise

    def get_recent_telemetry(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Grafik / özet için yalnızca gerekli sütunlar: zaman, bileşen, sensör, metrik, değer.

        ``ORDER BY timestamp DESC`` (en yeni önce).
        """
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                SELECT timestamp, component, sensor, metric, value
                FROM telemetry
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as exc:
            self._logger.exception("get_recent_telemetry: %s", exc)
            raise

    def recent_telemetry(self, limit: int = 100) -> List[dict[str, Any]]:
        """Tam satır; sıralama ``id`` (geriye uyumluluk). Yeni kod için :meth:`get_recent_telemetry` tercih edin."""
        conn = self._db.connection
        try:
            cur = conn.execute(
                "SELECT * FROM telemetry ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as exc:
            self._logger.exception("recent_telemetry: %s", exc)
            raise

    def get_daily_incidents(self) -> List[Dict[str, Any]]:
        """
        Bugün (UTC takvim günü) kayıtlı alarmlar; önem orta ve üstü (warning/critical eşdeğeri).

        ``incidents`` görünümü = ``alerts`` tablosu; ``component`` / ``details`` ``payload_json`` içinden.
        Dönüş: ``created_at``, ``timestamp``, ``severity``, ``title``, ``payload`` (tam JSON).
        """
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                SELECT created_at, title, severity, payload_json
                FROM incidents
                WHERE date(created_at) = date('now')
                  AND severity >= 20
                ORDER BY created_at DESC
                """,
            )
            rows: List[Dict[str, Any]] = []
            for r in cur.fetchall():
                d = dict(r)
                payload: Dict[str, Any] = {}
                raw = d.get("payload_json")
                if raw:
                    try:
                        payload = json.loads(str(raw))
                    except json.JSONDecodeError:
                        payload = {}
                ts = str(d.get("created_at", ""))
                rows.append(
                    {
                        "created_at": ts,
                        "timestamp": ts,
                        "severity": d.get("severity"),
                        "title": d.get("title"),
                        "component": payload.get("component", ""),
                        "details": payload.get("details", payload),
                        "payload": payload,
                    },
                )
            return rows
        except sqlite3.Error as exc:
            self._logger.exception("get_daily_incidents: %s", exc)
            raise

    def recent_rows_for_component_metric(
        self,
        component: str,
        metric: str,
        limit: int = 10,
    ) -> List[dict[str, Any]]:
        """
        Belirli bileşen + metrik için en son ``limit`` satır, kronolojik sırada
        (eskiden yeniye, trend analizi için).
        """
        conn = self._db.connection
        comp = str(component).strip().lower()
        met = str(metric).strip().lower()
        try:
            cur = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM telemetry
                    WHERE lower(component) = ? AND lower(metric) = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) AS t
                ORDER BY id ASC
                """,
                (comp, met, limit),
            )
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as exc:
            self._logger.exception("recent_rows_for_component_metric: %s", exc)
            raise

    def list_telemetry_between(
        self,
        start_iso: str,
        end_iso: str,
    ) -> List[Dict[str, Any]]:
        """
        ``telemetry`` tablosu: ``timestamp`` metin aralığında (ISO) satırlar, eskiden yeniye.
        """
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                SELECT * FROM telemetry
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC, id ASC
                """,
                (start_iso, end_iso),
            )
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.Error as exc:
            self._logger.exception("list_telemetry_between: %s", exc)
            raise

    def list_telemetry_snapshots_between(
        self,
        start_iso: str,
        end_iso: str,
    ) -> List[Dict[str, Any]]:
        """
        ``telemetry_snapshots`` kayıtları (``generated_at`` aralığı), ``payload`` ayrıştırılmış.

        Tam döngü raporunda ``health.component_scores`` gibi alanlar için kullanılır.
        """
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                SELECT id, generated_at, payload_json
                FROM telemetry_snapshots
                WHERE generated_at >= ? AND generated_at < ?
                ORDER BY generated_at ASC
                """,
                (start_iso, end_iso),
            )
            out: List[Dict[str, Any]] = []
            for row in cur.fetchall():
                d = dict(row)
                raw = d.get("payload_json")
                if raw:
                    try:
                        d["payload"] = json.loads(str(raw))
                    except json.JSONDecodeError:
                        d["payload"] = {}
                else:
                    d["payload"] = {}
                out.append(d)
            return out
        except sqlite3.Error as exc:
            self._logger.exception("list_telemetry_snapshots_between: %s", exc)
            raise

    def list_incidents_between(
        self,
        start_iso: str,
        end_iso: str,
    ) -> List[Dict[str, Any]]:
        """
        ``incidents`` görünümü (``alerts`` ile aynı) üzerinden zaman aralığı.

        ``created_at`` ISO metin; ``payload`` ayrıştırılmış eklenir.
        """
        conn = self._db.connection
        try:
            cur = conn.execute(
                """
                SELECT * FROM incidents
                WHERE created_at >= ? AND created_at < ?
                ORDER BY created_at ASC
                """,
                (start_iso, end_iso),
            )
            rows: List[Dict[str, Any]] = [dict(r) for r in cur.fetchall()]
            for row in rows:
                raw = row.get("payload_json")
                if raw:
                    try:
                        row["payload"] = json.loads(str(raw))
                    except json.JSONDecodeError:
                        row["payload"] = {}
                else:
                    row["payload"] = {}
            return rows
        except sqlite3.Error as exc:
            self._logger.exception("list_incidents_between: %s", exc)
            raise

    def insert_snapshot_package(self, payload: Dict[str, Any]) -> int:
        """Tam döngü raporunu telemetry_snapshots tablosuna yazar."""
        try:
            health = payload.get("health")
            score = 0.0
            if isinstance(health, dict) and "score" in health:
                score = float(health["score"])
            raw = json.dumps(payload, ensure_ascii=False, default=str)
            conn = self._db.connection
            gen = str(payload.get("generated_at", ""))
            cur = conn.execute(
                """
                INSERT INTO telemetry_snapshots (generated_at, payload_json)
                VALUES (?, ?)
                """,
                (gen, raw),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            self._logger.exception("insert_snapshot_package: %s", exc)
            raise

    @staticmethod
    def _parse_row_timestamp(ts: str) -> datetime:
        s = str(ts).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s)

    def rollup_telemetry_older_than_hours(self, hours: float = 1.0) -> Tuple[int, int]:
        """
        ``hours`` saatten eski ham ``telemetry`` satırlarını saatlik kovalara göre
        ortalar, ``telemetry_aggregates`` tablosuna yazar ve ham satırları siler.

        Dönüş: (eklenen özet satırı sayısı, silinen ham satır sayısı).
        """
        conn = self._db.connection
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        try:
            cur = conn.execute(
                """
                SELECT id, timestamp, component, sensor, metric, value, unit, status, source
                FROM telemetry
                WHERE timestamp < ?
                ORDER BY id ASC
                """,
                (cutoff_iso,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.Error as exc:
            self._logger.exception("rollup_telemetry: okuma: %s", exc)
            raise

        if not rows:
            return 0, 0

        groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            try:
                dt = self._parse_row_timestamp(str(r["timestamp"]))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                dt = dt.replace(minute=0, second=0, microsecond=0)
                bucket = dt.isoformat()
            except (TypeError, ValueError):
                bucket = str(r["timestamp"])[:13] + ":00:00+00:00"
            key = (
                bucket,
                str(r["component"]),
                str(r["sensor"]),
                str(r["metric"]),
            )
            groups[key].append(r)

        inserted = 0
        for (bucket, comp, sens, met), grp in groups.items():
            vals = [float(x["value"]) for x in grp]
            if not vals:
                continue
            n = len(vals)
            avg_v = sum(vals) / n
            min_v = min(vals)
            max_v = max(vals)
            unit = str(grp[0]["unit"])
            st = str(grp[0]["status"])
            src = "rollup"
            try:
                conn.execute(
                    """
                    INSERT INTO telemetry_aggregates (
                        bucket_hour, component, sensor, metric,
                        avg_value, min_value, max_value, sample_count,
                        unit, status, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bucket,
                        comp,
                        sens,
                        met,
                        avg_v,
                        min_v,
                        max_v,
                        n,
                        unit,
                        st,
                        src,
                    ),
                )
                inserted += 1
            except sqlite3.Error as exc:
                self._logger.warning("rollup insert atlandi: %s", exc)

        ids = [int(r["id"]) for r in rows]
        deleted = 0
        chunk = 400
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            placeholders = ",".join("?" * len(part))
            try:
                conn.execute(f"DELETE FROM telemetry WHERE id IN ({placeholders})", part)
                deleted += len(part)
            except sqlite3.Error as exc:
                self._logger.exception("rollup delete: %s", exc)
                raise
        conn.commit()
        if inserted or deleted:
            self._logger.info(
                "Telemetry rollup: %s ozet satiri, %s ham satir silindi (>%s h eski)",
                inserted,
                deleted,
                hours,
            )
        return inserted, deleted
