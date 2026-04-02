"""Sistem Telemetri — giriş noktası ve Orchestrator boru hattı."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.alerts.alert_engine import AlertEngine, alarm_severity_to_int
from core.analyzers.analysis_context import AnalysisDataContext
from core.analyzers.context_builder import build_analysis_context
from core.analyzers.correlation_analyzer import CorrelationAnalyzer
from core.analyzers.performance_analyzer import PerformanceAnalyzer
from core.analyzers.telemetry_row_analyzer import TelemetryRowAnalyzer
from core.analyzers.thermal_analyzer import ThermalCorrelationAnalyzer
from core.collectors.cpu_collector import CpuCollector
from core.collectors.disk_collector import DiskCollector
from core.collectors.fan_collector import FanCollector
from core.collectors.gpu_collector import GpuCollector
from core.collectors.memory_collector import MemoryCollector
from core.collectors.motherboard_collector import MotherboardCollector
from core.reporting.export_json import JsonExporter
from core.reporting.report_builder import ReportBuilder
from core.scoring.health_score import HealthScoreCalculator
from core.telemetry_schema import (
    COMPONENT_CPU,
    METRIC_TEMPERATURE,
    STATUS_NORMAL,
    enrich_readings_with_component_health,
)
from integrations.librehardwaremonitor.lhm_adapter import LhmAdapter
from storage.db import Database
from storage.models import AlertRecord
from storage.repository import TelemetryRepository
from ui.cli.dashboard_cli import DashboardCli
from utils.helpers import load_yaml, normalize_telemetry_bundle
from utils.logger import setup_logger, get_logger
from utils.time_utils import format_iso, utc_now


class Orchestrator:
    """
    Toplama → normalizasyon → analiz → uyarı → kalıcı kayıt → CLI gösterimi döngüsü.
    """

    def __init__(
        self,
        config_dir: Path,
        settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._config_dir = config_dir
        self._settings = settings or load_yaml(config_dir / "settings.yaml")
        self._logger = get_logger(f"{__name__}.Orchestrator")
        self._thresholds_path = config_dir / "thresholds.yaml"
        self._dashboard = DashboardCli()
        self._alert_engine = AlertEngine(self._thresholds_path)
        self._cycle_index = 0
        self._alarm_last_emit_mono: Dict[str, float] = {}
        self._performance_analyzer = PerformanceAnalyzer(self._thresholds_path)
        self._correlation_analyzer = CorrelationAnalyzer(self._thresholds_path)
        self._repo: Optional[TelemetryRepository] = None
        self._scoring_rules_path = self._config_dir / "scoring_rules.yaml"
        try:
            self._scoring_rules = load_yaml(self._scoring_rules_path)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("scoring_rules.yaml okunamadı: %s", exc)
            self._scoring_rules = {}

    def _poll_interval_seconds(self) -> float:
        app = self._settings.get("app", {})
        v = app.get("polling_interval_seconds")
        if v is None:
            v = app.get("poll_interval_seconds")
        try:
            return float(v if v is not None else 5.0)
        except (TypeError, ValueError) as exc:
            self._logger.warning("Geçersiz aralık, 5s kullanılıyor: %s", exc)
            return 5.0

    def _db_path(self) -> Path:
        st = self._settings.get("storage", {})
        raw = st.get("sqlite_path", "data/telemetry.db")
        p = Path(raw)
        return p if p.is_absolute() else (ROOT / p)

    def _repository(self) -> TelemetryRepository:
        if self._repo is None:
            db = Database(self._db_path())
            self._repo = TelemetryRepository(db)
        return self._repo

    def _lhm_adapter(self) -> Optional[LhmAdapter]:
        try:
            integ = self._settings.get("integrations", {})
            lhm_cfg = integ.get("libre_hardware_monitor", {})
            if not lhm_cfg.get("enabled", True):
                return None
            url = lhm_cfg.get("json_url")
            if not url:
                return None
            return LhmAdapter(json_url=str(url))
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("LHM adaptörü oluşturulamadı: %s", exc)
            return None

    def _alarm_cooldown_seconds(self) -> float:
        a = self._settings.get("alerts") or {}
        try:
            return max(0.0, float(a.get("cooldown_seconds", 300)))
        except (TypeError, ValueError):
            return 300.0

    def _telemetry_retention_settings(self) -> Dict[str, Any]:
        return self._settings.get("telemetry_retention") or {}

    @staticmethod
    def _physical_key_row(row: Dict[str, Any]) -> str:
        return "|".join(
            [
                str(row.get("component", "")).lower(),
                str(row.get("sensor", "")),
                str(row.get("metric", "")).lower(),
            ],
        )

    def _filter_alarms_cooldown(
        self,
        alarms: List[Dict[str, Any]],
        triggered_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Aynı sensör için tekrarlayan alarm kayıtlarını süre dolana kadar süz."""
        cooldown = self._alarm_cooldown_seconds()
        if cooldown <= 0 or not alarms:
            return alarms
        now = time.monotonic()
        physical_bad = {self._physical_key_row(r) for r in triggered_rows}
        for k in list(self._alarm_last_emit_mono.keys()):
            if k not in physical_bad:
                del self._alarm_last_emit_mono[k]

        out: List[Dict[str, Any]] = []
        for alarm, row in zip(alarms, triggered_rows):
            pk = self._physical_key_row(row)
            last = self._alarm_last_emit_mono.get(pk)
            if last is not None and (now - last) < cooldown:
                continue
            out.append(alarm)
            self._alarm_last_emit_mono[pk] = now
        return out

    def _maybe_rollup_telemetry(self) -> None:
        tr = self._telemetry_retention_settings()
        if not tr.get("enabled", True):
            return
        try:
            every = int(tr.get("run_every_cycles", 30))
        except (TypeError, ValueError):
            every = 30
        if every <= 0:
            return
        self._cycle_index += 1
        if self._cycle_index % every != 0:
            return
        try:
            hours = float(tr.get("rollup_after_hours", 1.0))
        except (TypeError, ValueError):
            hours = 1.0
        try:
            repo = self._repository()
            repo.rollup_telemetry_older_than_hours(hours)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Telemetry rollup atlandi: %s", exc)

    async def _collect_rows_async(self) -> List[Dict[str, Any]]:
        """Toplayıcıları paralel çalıştırır (yavaş sensör tüm döngüyü bloklamaz)."""
        ts = format_iso(utc_now())
        lhm = self._lhm_adapter()

        async def run_cpu() -> List[Dict[str, Any]]:
            return await asyncio.to_thread(
                lambda: CpuCollector().collect(interval=0.1, timestamp=ts),
            )

        async def run_mem() -> List[Dict[str, Any]]:
            return await asyncio.to_thread(lambda: MemoryCollector().collect(timestamp=ts))

        async def run_disk() -> List[Dict[str, Any]]:
            return await asyncio.to_thread(lambda: DiskCollector().collect(timestamp=ts))

        async def run_gpu() -> List[Dict[str, Any]]:
            return await asyncio.to_thread(lambda: GpuCollector(lhm).collect(timestamp=ts))

        async def run_fan() -> List[Dict[str, Any]]:
            return await asyncio.to_thread(lambda: FanCollector(lhm).collect(timestamp=ts))

        async def run_mb() -> List[Dict[str, Any]]:
            return await asyncio.to_thread(lambda: MotherboardCollector(lhm).collect(timestamp=ts))

        results = await asyncio.gather(
            run_cpu(),
            run_mem(),
            run_disk(),
            run_gpu(),
            run_fan(),
            run_mb(),
            return_exceptions=True,
        )
        rows: List[Dict[str, Any]] = []
        labels = ("cpu", "memory", "disk", "gpu", "fan", "motherboard")
        for label, res in zip(labels, results):
            if isinstance(res, Exception):
                self._logger.warning("Collector %s basarisiz: %s", label, res)
                continue
            rows.extend(res)
        return rows

    def _collect_rows(self) -> List[Dict[str, Any]]:
        """Tüm toplayıcılardan birleşik şema telemetri satırları (asyncio paralel)."""
        try:
            return asyncio.run(self._collect_rows_async())
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Toplama hatası: %s", exc)
            raise

    def _analyze_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        data_context: Optional[AnalysisDataContext] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Katman A: eşik (TelemetryRowAnalyzer).
        Katman A2: termal korelasyon — depo geçmişi + anlık (ThermalCorrelationAnalyzer).
        Katman B: CPU sıcaklığı rolling window + trend (PerformanceAnalyzer).
        Katman C: korelasyon (CorrelationAnalyzer).
        """
        analyzer = TelemetryRowAnalyzer(self._thresholds_path)
        analyzed = analyzer.analyze_rows(rows)
        ctx = data_context or AnalysisDataContext(current_readings=list(rows))
        thermal_corr = ThermalCorrelationAnalyzer(self._scoring_rules)
        cooling = thermal_corr.analyze_cooling_issue(ctx, analyzed)
        analyzed = ThermalCorrelationAnalyzer.apply_cooling_issue_to_readings(
            analyzed,
            cooling,
        )
        non_normal = sum(1 for r in analyzed if r.get("status") != STATUS_NORMAL)

        for r in analyzed:
            if (
                str(r.get("component", "")).lower() == COMPONENT_CPU
                and str(r.get("metric", "")).lower() == METRIC_TEMPERATURE
            ):
                try:
                    self._performance_analyzer.record_cpu_temperature_sample(
                        str(r["timestamp"]),
                        float(r["value"]),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._logger.debug("CPU sıcaklık örneği atlandı: %s", exc)
                break

        trend = self._performance_analyzer.cpu_temperature_trend()
        ctx = CorrelationAnalyzer.build_context_from_readings(analyzed)
        ctx["cpu_temp_trend"] = trend
        corr = self._correlation_analyzer.analyze(ctx)

        analysis = {
            "readings_total": len(analyzed),
            "readings_non_normal": non_normal,
            "layer_a_threshold": {
                "name": "threshold",
                "description": "Anlık eşik denetimi (thresholds.yaml)",
                "engine": "TelemetryRowAnalyzer",
            },
            "layer_b_trend": {
                **self._performance_analyzer.trend_snapshot(),
                "cpu_temperature_trend": trend,
                "description": "Son 5 dk rolling window + doğrusal eğim (°C/s)",
                "engine": "PerformanceAnalyzer / RollingWindow",
            },
            "layer_c_correlation": {
                "findings": [asdict(f) for f in corr],
                "description": "Çoklu sensör çapraz kontrol",
                "engine": "CorrelationAnalyzer",
            },
            "layer_thermal_timeseries": {
                "description": "Depo geçmişi + anlık: yük, sıcaklık, fan (soğutma tutarlılığı)",
                "engine": "ThermalCorrelationAnalyzer",
                "cooling_issue": asdict(cooling),
            },
        }
        return analyzed, analysis

    def _emit_enriched_alarms(
        self,
        analyzed: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Zenginleştirilmiş alarm JSON kayıtları üretir ve loglar."""
        triggered = [r for r in analyzed if r.get("status") != STATUS_NORMAL]
        alarms = self._alert_engine.build_enriched_alarm_records(triggered, analyzed)
        alarms = self._filter_alarms_cooldown(alarms, triggered)
        for alarm in alarms:
            try:
                self._logger.warning(
                    "ALARM [%s] %s — %s",
                    alarm.get("severity"),
                    alarm.get("code"),
                    alarm.get("title"),
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.error("Alarm loglama hatası: %s", exc)
        return alarms

    def _persist(
        self,
        report: Dict[str, Any],
        alarms: List[Dict[str, Any]],
        readings: List[Dict[str, Any]],
    ) -> None:
        """Telemetri satırları, tam paket özeti ve zengin alarm kayıtları."""
        try:
            repo = self._repository()
            repo.insert_telemetry_rows(readings)
            repo.insert_snapshot_package(report)
            for alarm in alarms:
                rec = AlertRecord(
                    title=str(alarm.get("title", "")),
                    severity=alarm_severity_to_int(str(alarm.get("severity", "medium"))),
                    payload_json=json.dumps(alarm, ensure_ascii=False),
                )
                repo.insert_alert(rec)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Kalıcı kayıt hatası: %s", exc)
            raise

    def run_cycle(
        self,
        *,
        dashboard_live: bool = True,
        clear_screen: bool = True,
    ) -> Dict[str, Any]:
        """Tek döngü: topla → analiz (status) → uyarı → DB → CLI."""
        raw_rows = self._collect_rows()
        repo = self._repository()
        data_ctx = build_analysis_context(repo, raw_rows, history_limit=10)
        analyzed, analysis = self._analyze_rows(raw_rows, data_context=data_ctx)
        scorer = HealthScoreCalculator(
            settings=self._settings,
            scoring_rules=self._scoring_rules,
        )
        health = scorer.compute_from_readings(analyzed)
        analyzed = enrich_readings_with_component_health(analyzed, health.component_scores)
        bundle = normalize_telemetry_bundle(analyzed)

        alarms = self._emit_enriched_alarms(analyzed)

        health_dict = {
            "score": health.score,
            "component_scores": health.component_scores,
            "reasons": health.reasons,
            "factors": health.factors,
            "summary": health.summary,
        }

        builder = ReportBuilder()
        report = builder.build(
            collectors_data={
                "readings": analyzed,
                "bundle": bundle,
            },
            analysis=analysis,
            health_score=health_dict,
            alerts=alarms,
        )

        self._persist(report, alarms, analyzed)
        self._maybe_rollup_telemetry()

        if dashboard_live:
            if clear_screen:
                self._dashboard.print_live(report, clear=True)
            else:
                self._dashboard.print_report(report)

        return report

    def run_forever(self) -> None:
        """polling_interval_seconds kadar bekleyerek sürekli döngü."""
        interval = self._poll_interval_seconds()
        self._logger.info(
            "Orchestrator başladı (aralık: %.1f s). Durdurmak için Ctrl+C.",
            interval,
        )
        try:
            while True:
                try:
                    self.run_cycle(dashboard_live=True, clear_screen=True)
                except Exception as exc:  # noqa: BLE001
                    self._logger.exception("Döngü hatası: %s", exc)
                time.sleep(interval)
        except KeyboardInterrupt:
            self._logger.info("Orchestrator durduruldu.")

    def run_once(
        self,
        *,
        export_json: bool = False,
        output_dir: Optional[Path] = None,
        show_dashboard: bool = True,
    ) -> Tuple[Dict[str, Any], Optional[Path]]:
        """Tek tur; isteğe bağlı JSON raporu."""
        report = self.run_cycle(dashboard_live=show_dashboard, clear_screen=False)
        report_path: Optional[Path] = None
        if export_json and output_dir is not None:
            settings = self._settings
            out = output_dir
            if isinstance(settings.get("reporting"), dict):
                rp = settings["reporting"].get("output_dir")
                if rp:
                    out = ROOT / Path(rp) if not Path(rp).is_absolute() else Path(rp)
            out.mkdir(parents=True, exist_ok=True)
            report_path = out / "latest_report.json"
            JsonExporter().export(report, report_path)
            self._logger.info("Report written: %s", report_path)
        return report, report_path


def _resolve_db_path(config_dir: Path, settings: Dict[str, Any]) -> Path:
    st = settings.get("storage", {})
    raw = st.get("sqlite_path", "data/telemetry.db")
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p)


def run_full_report_pipeline(
    config_dir: Path,
    reports_dir: Path,
) -> tuple[Path, Path]:
    """
    Tam tetik: (1) Collector ile telemetri topla, (2) Analyzer + Storage ile DB'ye yaz,
    (3) donanım + özet + grafik + olaylar içeren tarihli PDF üret.

    Donanım bilgisi PDF içinde ``add_hardware_inventory_section`` ile gömülür
    (``get_system_hardware`` / ``collect_hardware_inventory``).
    """
    logger = setup_logger("system_telemetry.main")
    th = config_dir / "thresholds.yaml"
    if not th.is_file():
        logger.warning("thresholds.yaml bulunamadi: %s", th)

    try:
        settings = load_yaml(config_dir / "settings.yaml")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ayarlar okunamadi, varsayilanlar: %s", exc)
        settings = {}

    print("Rapor olusturuluyor (telemetri toplama, analiz, DB yazimi)...")
    orch = Orchestrator(config_dir, settings)
    _, json_path = orch.run_once(
        export_json=True,
        output_dir=reports_dir,
        show_dashboard=False,
    )
    if json_path is None:
        raise RuntimeError("JSON rapor yolu uretilemedi")

    db_path = _resolve_db_path(config_dir, settings)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    from core.reporting.export_pdf import fpdf_dated_report_filename
    from core.reporting.report_trigger import run_gunluk_ozet_pdf

    print("PDF raporu uretiliyor...")
    pdf_path = reports_dir / fpdf_dated_report_filename()
    pdf_path = run_gunluk_ozet_pdf(db_path, pdf_path)
    print(f"Rapor hazir: {pdf_path}")
    return json_path, pdf_path


def run_snapshot(
    config_dir: Path,
    output_dir: Path,
) -> Path:
    """Tek seferlik ölçüm ve JSON rapor üretir (Orchestrator)."""
    logger = setup_logger("system_telemetry.main")
    th = config_dir / "thresholds.yaml"
    if not th.is_file():
        logger.warning("thresholds.yaml bulunamadi: %s", th)

    try:
        settings = load_yaml(config_dir / "settings.yaml")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ayarlar okunamadi, varsayilanlar: %s", exc)
        settings = {}

    orch = Orchestrator(config_dir, settings)
    _, path = orch.run_once(export_json=True, output_dir=output_dir, show_dashboard=False)
    if path is None:
        raise RuntimeError("Rapor yolu uretilemedi")
    return path


def main() -> None:
    """CLI."""
    parser = argparse.ArgumentParser(description="Sistem Telemetri Izleme")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config",
        help="Yapilandirma dizini",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "reports",
        help="Rapor cikti dizini (--once icin)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Tek tur Orchestrator + JSON rapor; surekli dongu yok",
    )
    parser.add_argument(
        "--pdf-report",
        action="store_true",
        help="Sadece DB'den gunluk FPDF ozet (cikti: --out / REPORT_YYYY_MM_DD.pdf)",
    )
    parser.add_argument(
        "--full-report",
        action="store_true",
        help="Tek tur: telemetri + analiz + DB + tarihli PDF (tam pipeline)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Flask web panelini arka planda başlat (varsayılan 0.0.0.0:5000); veri toplama ana döngüde sürer",
    )
    parser.add_argument(
        "--web-host",
        default="0.0.0.0",
        help="--web ile dinlenecek adres (yerel: 127.0.0.1, ağ: 0.0.0.0)",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=5000,
        help="--web ile dinlenecek port",
    )
    args = parser.parse_args()

    try:
        setup_logger("system_telemetry.main")
        if args.full_report:
            reports_dir = Path(args.out)
            run_full_report_pipeline(args.config, reports_dir)
        elif args.pdf_report:
            try:
                settings = load_yaml(args.config / "settings.yaml")
            except Exception as exc:  # noqa: BLE001
                setup_logger("system_telemetry.main").warning(
                    "Ayarlar okunamadi, varsayilanlar: %s", exc
                )
                settings = {}
            db_path = _resolve_db_path(args.config, settings)
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            from core.reporting.export_pdf import fpdf_dated_report_filename
            from core.reporting.report_trigger import run_gunluk_ozet_pdf

            pdf_path = out_dir / fpdf_dated_report_filename()
            path = run_gunluk_ozet_pdf(db_path, pdf_path)
            print(f"PDF: {path}")
        elif args.once:
            path = run_snapshot(args.config, args.out)
            print(f"Tamamlandi: {path}")
        else:
            settings = load_yaml(args.config / "settings.yaml")
            orch = Orchestrator(args.config, settings)
            if args.web:
                from ui.web.app import start_flask_background_thread

                start_flask_background_thread(
                    orch._db_path(),
                    host=str(args.web_host),
                    port=int(args.web_port),
                )
                log = setup_logger("system_telemetry.main")
                log.info(
                    "Web panel dinleniyor: http://%s:%s/ (yerel: http://127.0.0.1:%s/)",
                    args.web_host,
                    args.web_port,
                    args.web_port,
                )
                print(
                    f"Matrix Core aktif — panel: http://127.0.0.1:{args.web_port}/ "
                    f"(dinleme {args.web_host}:{args.web_port})",
                    flush=True,
                )
            orch.run_forever()
    except Exception as exc:  # noqa: BLE001
        setup_logger("system_telemetry.main").exception("Calistirma hatasi: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
