"""SQLite bağlantı ve şema — iş parçacığı başına bağlantı (Flask + Orchestrator güvenli)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from utils.logger import get_logger


class Database:
    """
    SQLite sarmalayıcı.

    Her iş parçacığı kendi ``sqlite3`` bağlantısını kullanır; aksi halde
    ``ProgrammingError: SQLite objects created in a thread can only be used in that same thread``
    hatası oluşur (Flask ``threaded=True`` + ana döngü aynı dosyaya erişince).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._local = threading.local()
        # İlk bağlantı + şema: iki iş parçacığı aynı anda açılırsa "table already exists" önlenir.
        self._init_lock = threading.Lock()
        self._logger = get_logger(f"{__name__}.Database")

    def _conn(self) -> Optional[sqlite3.Connection]:
        return getattr(self._local, "conn", None)

    def connect(self) -> sqlite3.Connection:
        """Bu iş parçacığı için bağlantı açar ve şemayı oluşturur."""
        existing = self._conn()
        if existing is not None:
            return existing
        with self._init_lock:
            if self._conn() is not None:
                return self._conn()  # type: ignore[return-value]
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # Aynı iş parçacığında kalır; farklı iş parçacıkları ayrı ``connect()`` ile açar.
                conn = sqlite3.connect(str(self._path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self._local.conn = conn
                self._init_schema()
                return conn
            except sqlite3.Error as exc:
                self._logger.exception("SQLite bağlantı hatası: %s", exc)
                raise

    def _init_schema(self) -> None:
        c = self._conn()
        if c is None:
            return
        try:
            self._ensure_telemetry_table()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    severity INTEGER NOT NULL,
                    acknowledged INTEGER DEFAULT 0,
                    payload_json TEXT
                );
                CREATE VIEW IF NOT EXISTS incidents AS
                    SELECT * FROM alerts;
                CREATE TABLE IF NOT EXISTS health_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    score REAL NOT NULL,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS telemetry_aggregates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket_hour TEXT NOT NULL,
                    component TEXT NOT NULL,
                    sensor TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    avg_value REAL NOT NULL,
                    min_value REAL,
                    max_value REAL,
                    sample_count INTEGER NOT NULL,
                    unit TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'rollup'
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp);
                """
            )
            c.commit()
        except sqlite3.Error as exc:
            self._logger.exception("Şema oluşturma hatası: %s", exc)
            raise

    def _ensure_telemetry_table(self) -> None:
        """Birleşik JSON şemasına uygun telemetry tablosu (eski şemada DROP + yeniden oluştur)."""
        c = self._conn()
        assert c is not None
        cur = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='telemetry'",
        )
        if not cur.fetchone():
            self._create_telemetry_table()
            return

        cols = {r[1] for r in c.execute("PRAGMA table_info(telemetry)")}
        if "sensor" in cols and "metric" in cols and "status" in cols:
            return

        self._logger.info("telemetry tablosu yeni şemaya geciriliyor (eski satirlar silinir).")
        c.execute("DROP TABLE IF EXISTS telemetry")
        self._create_telemetry_table()

    def _create_telemetry_table(self) -> None:
        c = self._conn()
        assert c is not None
        c.execute(
            """
            CREATE TABLE telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                component TEXT NOT NULL,
                sensor TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL
            );
            """,
        )

    @property
    def connection(self) -> sqlite3.Connection:
        """Aktif iş parçacığı için bağlantı; yoksa açar."""
        if self._conn() is None:
            return self.connect()
        return self._conn()  # type: ignore[return-value]

    def close(self) -> None:
        """Bu iş parçacığındaki bağlantıyı kapatır."""
        try:
            c = self._conn()
            if c is not None:
                c.close()
                self._local.conn = None
        except sqlite3.Error as exc:
            self._logger.warning("Kapatma hatası: %s", exc)
