"""Flask arka uç: canlı telemetri JSON, günlük PDF indirme, CORS."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, send_file
from flask_cors import CORS

from core.reporting.export_pdf import fpdf_dated_report_filename, get_latest_health_from_repository
from core.reporting.hardware_inventory import collect_hardware_inventory
from core.reporting.report_trigger import run_gunluk_ozet_pdf
from core.telemetry_schema import COMPONENT_CPU, COMPONENT_GPU, METRIC_TEMPERATURE
from storage.db import Database
from storage.repository import TelemetryRepository
from utils.helpers import load_yaml
from utils.logger import get_logger

_ROOT = Path(__file__).resolve().parents[2]


def _default_db_path() -> Path:
    try:
        settings = load_yaml(_ROOT / "config" / "settings.yaml")
    except Exception:
        settings = {}
    st = settings.get("storage", {})
    raw = st.get("sqlite_path", "data/telemetry.db")
    p = Path(raw)
    return p if p.is_absolute() else (_ROOT / p)


def _default_reports_dir() -> Path:
    try:
        settings = load_yaml(_ROOT / "config" / "settings.yaml")
    except Exception:
        settings = {}
    rp = (settings.get("reporting") or {}).get("output_dir", "reports")
    p = Path(str(rp))
    return p if p.is_absolute() else (_ROOT / p)


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def build_temperature_chart_payload(rows: List[Dict[str, Any]], max_points: int = 80) -> Dict[str, Any]:
    """
    Ham telemetri satırlarından CPU/GPU sıcaklık serileri (zaman ekseni kronolojik).
    Aynı (timestamp, component) için birden fazla sensör varsa en yüksek değer alınır.
    """
    bucket: Dict[str, Dict[str, float]] = defaultdict(dict)
    for r in rows:
        if str(r.get("metric", "")).lower() != METRIC_TEMPERATURE:
            continue
        comp = str(r.get("component", "")).lower()
        if comp not in (COMPONENT_CPU, COMPONENT_GPU):
            continue
        ts = str(r.get("timestamp", ""))
        if not ts:
            continue
        try:
            val = float(r["value"])
        except (KeyError, TypeError, ValueError):
            continue
        cur = bucket[ts].get(comp)
        if cur is None or val > cur:
            bucket[ts][comp] = val

    ordered_ts = sorted(bucket.keys(), key=lambda s: _parse_ts(s) or datetime.min)
    if len(ordered_ts) > max_points:
        ordered_ts = ordered_ts[-max_points:]

    labels: List[str] = []
    cpu_vals: List[Optional[float]] = []
    gpu_vals: List[Optional[float]] = []
    for ts in ordered_ts:
        dt = _parse_ts(ts)
        labels.append(dt.strftime("%H:%M:%S") if dt else ts[:8])
        d = bucket[ts]
        cpu_vals.append(d.get(COMPONENT_CPU))
        gpu_vals.append(d.get(COMPONENT_GPU))

    return {
        "labels": labels,
        "cpu_temperature": cpu_vals,
        "gpu_temperature": gpu_vals,
    }


def create_app(db_path: Optional[Path] = None) -> Flask:
    """Flask uygulama fabrikası. ``db_path`` verilmezse ``config/settings.yaml`` → ``sqlite_path``."""
    base = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(base / "templates"),
        static_folder=str(base / "static"),
    )
    CORS(app)
    log = get_logger(f"{__name__}.create_app")

    resolved = (db_path or _default_db_path()).resolve()
    reports_dir = _default_reports_dir().resolve()
    db = Database(resolved)
    repo = TelemetryRepository(db)

    @app.route("/")
    def index() -> str:
        try:
            return render_template(
                "index.html",
                title="Sistem Telemetri",
                report_pdf_filename=fpdf_dated_report_filename(),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Şablon hatası: %s", exc)
            return "<h1>Sistem Telemetri</h1><p>Şablon yüklenemedi.</p>"

    @app.route("/api/live-data")
    @app.route("/api/telemetry")
    def live_data() -> tuple:
        """SQLite son telemetri + CPU/GPU sıcaklık serisi (Chart.js). ``/api/telemetry`` ile aynı."""
        try:
            raw = repo.get_recent_telemetry(3000)
            series = build_temperature_chart_payload(raw)
            return jsonify(
                {
                    "series": series,
                    "raw_rows": raw[:200],
                    "db_path": str(resolved),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("live-data: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/integration-status")
    def integration_status() -> tuple:
        """LHM JSON URL ve etkinlik — panelde/config ile port eşleşmesi kontrolü için."""
        try:
            settings = load_yaml(_ROOT / "config" / "settings.yaml")
        except Exception:  # noqa: BLE001
            settings = {}
        integ = settings.get("integrations") or {}
        lhm = integ.get("libre_hardware_monitor") or {}
        return jsonify(
            {
                "libre_hardware_monitor": {
                    "enabled": bool(lhm.get("enabled", True)),
                    "json_url": str(lhm.get("json_url") or ""),
                    "hint": "LibreHardwareMonitor: Options → Remote Web Server → Run (port genelde 8085; url ayarla).",
                },
            },
        )

    @app.route("/api/health")
    def api_health() -> tuple:
        """Son 24 saatteki anlık görüntülerden güncel ``payload.health``."""
        try:
            payload = get_latest_health_from_repository(repo)
            return jsonify(payload)
        except Exception as exc:  # noqa: BLE001
            log.exception("health API: %s", exc)
            return (
                jsonify(
                    {
                        "score": None,
                        "pending": True,
                        "reasons": ["Sağlık verisi okunamadı; bir süre sonra yeniden deneyin."],
                        "error": str(exc),
                    },
                ),
                500,
            )

    @app.route("/api/hardware")
    def api_hardware() -> tuple:
        """Sistem donanım özeti (CPU, GPU, RAM, OS) — panel tablosu için."""
        try:
            return jsonify(collect_hardware_inventory())
        except Exception as exc:  # noqa: BLE001
            log.exception("hardware API: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/reports/daily-pdf", methods=["GET"])
    def download_daily_pdf() -> tuple:
        """``run_gunluk_ozet_pdf`` ile günlük PDF üretir ve tarayıcıya indirir."""
        try:
            reports_dir.mkdir(parents=True, exist_ok=True)
            out_path = reports_dir / fpdf_dated_report_filename()
            out_path = run_gunluk_ozet_pdf(resolved, out_path)
            if not out_path.is_file():
                return jsonify({"error": "PDF dosyası oluşturulamadı."}), 500
            return send_file(
                str(out_path),
                as_attachment=True,
                download_name=out_path.name,
                mimetype="application/pdf",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("daily-pdf: %s", exc)
            return jsonify({"error": str(exc)}), 500

    return app


def start_flask_background_thread(
    db_path: Optional[Path] = None,
    *,
    host: str = "0.0.0.0",
    port: int = 5000,
) -> threading.Thread:
    """
    Flask'ı ayrı bir iş parçacığında başlatır (ana iş parçacığı Orchestrator için serbest kalır).
    ``debug`` / reloader kapalı — çoklu süreç ve iş parçacığı karışmaz.
    """

    def _run() -> None:
        app = create_app(db_path)
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)

    t = threading.Thread(target=_run, name="flask-web", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Sistem Telemetri Web Paneli")
    parser.add_argument("--host", default="0.0.0.0", help="Dinleme adresi (yerel: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--with-collector",
        action="store_true",
        help="Veri toplama döngüsünü (Orchestrator) arka planda başlat",
    )
    args = parser.parse_args()

    if args.with_collector:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))

        def _collector() -> None:
            from utils.helpers import load_yaml as _ly

            from main import Orchestrator

            cfg = _ROOT / "config"
            settings = _ly(cfg / "settings.yaml") if (cfg / "settings.yaml").is_file() else {}
            Orchestrator(cfg, settings).run_forever()

        threading.Thread(target=_collector, name="orchestrator", daemon=True).start()

    create_app().run(
        host=args.host,
        port=args.port,
        debug=True,
        threaded=True,
        use_reloader=False,
    )
