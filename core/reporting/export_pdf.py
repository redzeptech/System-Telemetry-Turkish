"""PDF raporları — ReportLab + matplotlib; FPDF2 + matplotlib (SystemReport); SQLite."""

from __future__ import annotations

import io
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from xml.sax.saxutils import escape

from core.telemetry_schema import METRIC_MEMORY_USAGE, METRIC_TEMPERATURE
from storage.db import Database
from storage.repository import TelemetryRepository
from utils.logger import get_logger

# Matplotlib başka modüllerden önce Agg (GUI yok)
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover
    matplotlib = None  # type: ignore[assignment]
    mdates = None  # type: ignore[assignment]
    plt = None  # type: ignore[assignment]

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Image as RLImage,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:  # pragma: no cover
    pdfmetrics = None  # type: ignore[assignment]

try:
    from fpdf import FPDF
except ImportError:  # pragma: no cover
    FPDF = None  # type: ignore[misc, assignment]

_LOGGER = get_logger(__name__)

# Tasarım: lacivert başlık, kritik metin kırmızı
COLOR_NAVY = colors.HexColor("#1a237e")
COLOR_CRITICAL_TEXT = colors.HexColor("#c62828")
COLOR_FOOTER = colors.HexColor("#455a64")
FOOTER_BRAND = "Sistem Telemetri Raporu - 2026"

_SEVERITY_TR = {
    40: "Kritik",
    30: "Yüksek",
    20: "Orta",
    10: "Düşük",
}

_CODE_HINTS_TR: Dict[str, str] = {
    "COOLING_ISSUE": (
        "Soğutucu montajı, termal macun ve kasa hava akışını kontrol edin; "
        "BIOS’ta fan eğrilerini gözden geçirin."
    ),
    "THERMAL_CONTACT_OR_PUMP_SUSPECT": (
        "Sıvı soğutucu ise pompa/devir; hava soğutucu ise montaj baskısı ve macun ömrü."
    ),
    "INSUFFICIENT_COOLING_CAPACITY": (
        "Kasa fan kapasitesi ve toz birikimi; gerekirse fan yükseltmesi veya kasa değişimi."
    ),
    "CPU_TEMP_CRITICAL": "Acil: yükü düşürün, soğutucu ve fanları doğrulayın.",
    "GPU_TEMP_CRITICAL": "GPU yükünü azaltın; soğutucu temasını ve sıcak hava tahliyesini kontrol edin.",
    "DISK_USAGE_CRITICAL": "Disk alanı açın; büyük dosya ve günlükleri temizleyin veya depolama ekleyin.",
    "FAN_SPEED_CRITICAL": "Fan kablosu, BIOS ayarı ve sensör okumasını kontrol edin.",
}


def _utc_day_bounds(d: date) -> Tuple[str, str]:
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _utc_last_24h_bounds() -> Tuple[str, str]:
    """Şu andan geriye doğru 24 saat (UTC)."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=24)
    return start.isoformat(), now.isoformat()


# Snapshot / health yokken: skor 0 yerine UI ve PDF "beklemede" gösterir (yanlış alarm önlenir).
PENDING_HEALTH_SNAPSHOT: Dict[str, Any] = {
    "score": None,
    "pending": True,
    "reasons": [
        "Henüz yeterli telemetri anlık görüntüsü yok; skor ilk döngülerde hesaplanacak.",
    ],
}


def get_latest_health_from_repository(repo: TelemetryRepository) -> Dict[str, Any]:
    """Son 24 saatteki ``telemetry_snapshots`` kayıtlarından en güncel ``payload.health``."""
    start_iso, end_iso = _utc_last_24h_bounds()
    snapshots = repo.list_telemetry_snapshots_between(start_iso, end_iso)
    if not snapshots:
        return dict(PENDING_HEALTH_SNAPSHOT)
    pl = snapshots[-1].get("payload") if isinstance(snapshots[-1].get("payload"), dict) else {}
    h = pl.get("health")
    if isinstance(h, dict) and h:
        return dict(h)
    return dict(PENDING_HEALTH_SNAPSHOT)


# health.component_scores anahtarları → grafik etiketi
_COMPONENT_LABELS: Tuple[Tuple[str, str], ...] = (
    ("cpu", "CPU"),
    ("gpu", "GPU"),
    ("memory", "RAM"),
)


def _parse_created_at(s: str) -> Optional[datetime]:
    try:
        t = str(s).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(t)
    except (TypeError, ValueError):
        return None


def _severity_label(sev: int) -> str:
    return _SEVERITY_TR.get(int(sev), f"Seviye {sev}")


def _incident_parse_details(details: Any, payload: Dict[str, Any]) -> Any:
    """``details`` string ise ve JSON gibi görünüyorsa ``json.loads`` ile sözlük/listeye çevir."""
    raw: Any = details
    if raw is None:
        raw = payload.get("details")
    if isinstance(raw, str):
        s = raw.strip()
        if len(s) >= 2 and (
            (s[0] == "{" and s[-1] == "}")
            or (s[0] == "[" and s[-1] == "]")
        ):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return raw
    return raw


def _incident_details_text(parsed: Any) -> str:
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, indent=0)
    if parsed is None or parsed == "":
        return "-"
    return str(parsed)


def _incident_is_critical(inc: Dict[str, Any]) -> bool:
    s = inc.get("severity")
    if isinstance(s, str) and s.strip().lower() in ("critical", "crit", "kritik"):
        return True
    try:
        return int(s) >= 40
    except (TypeError, ValueError):
        return False


def _incident_severity_display(inc: Dict[str, Any]) -> str:
    s = inc.get("severity")
    if isinstance(s, str):
        low = s.strip().lower()
        if low in ("critical", "crit", "kritik"):
            return "Kritik"
        return s[:14]
    try:
        return _severity_label(int(s))
    except (TypeError, ValueError):
        return str(s)[:14]


def _incident_row_status(inc: Dict[str, Any]) -> str:
    """Satır teması: ``critical`` | ``warning`` | ``normal``."""
    if _incident_is_critical(inc):
        return "critical"
    s = inc.get("severity")
    try:
        if int(s) >= 30:
            return "warning"
    except (TypeError, ValueError):
        if isinstance(s, str):
            low = s.lower()
            if any(x in low for x in ("warning", "yüksek", "orta", "high", "medium")):
                return "warning"
    return "normal"


def _badge_caption(row_st: str) -> str:
    """Rozet üzerinde kısa Türkçe etiket."""
    if row_st == "critical":
        return "KRITIK"
    if row_st == "warning":
        return "UYARI"
    return "NORMAL"


def _register_unicode_font() -> str:
    """ReportLab için TTF; önce matplotlib DejaVuSans."""
    assert pdfmetrics is not None
    if matplotlib is None:  # pragma: no cover
        return "Helvetica"
    try:
        font_path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
        if font_path.is_file():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(font_path)))
            return "DejaVuSans"
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("DejaVuSans kayıt edilemedi: %s", exc)
    return "Helvetica"


def _severity_counts(rows: Sequence[Dict[str, Any]]) -> Tuple[List[str], List[int]]:
    """X: Türkçe etiket, Y: adet."""
    buckets = {40: 0, 30: 0, 20: 0, 10: 0}
    for r in rows:
        try:
            s = int(r.get("severity", 20))
        except (TypeError, ValueError):
            s = 20
        if s in buckets:
            buckets[s] += 1
        else:
            buckets[20] += 1
    labels = [_SEVERITY_TR[k] for k in (40, 30, 20, 10)]
    counts = [buckets[k] for k in (40, 30, 20, 10)]
    return labels, counts


def _hourly_counts(rows: Sequence[Dict[str, Any]]) -> Tuple[List[int], List[int]]:
    """0–23 saat dilimleri ve her saat için olay sayısı."""
    hours = list(range(24))
    cnt = [0] * 24
    for r in rows:
        dt = _parse_created_at(str(r.get("created_at", "")))
        if dt is None:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        cnt[dt.hour] += 1
    return hours, cnt


def _use_report_matplotlib_style() -> None:
    """ggplot veya seaborn-v0_8 (hangisi varsa); yoksa varsayılan."""
    if plt is None:
        return
    for name in ("seaborn-v0_8", "ggplot"):
        try:
            plt.style.use(name)
            return
        except (OSError, ValueError, KeyError):
            continue


def _prepare_report_figure(
    figsize: Tuple[float, float],
    *,
    grid: bool = True,
) -> Tuple[Any, Any]:
    """Beyaz zemin; isteğe bağlı hafif ızgara (alpha=0.3)."""
    assert plt is not None
    _use_report_matplotlib_style()
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    if grid:
        ax.grid(True, alpha=0.3)
        ax.set_axisbelow(True)
    return fig, ax


def _savefig_report_png(fig: Any, buf: io.BytesIO, *, bbox_inches: Optional[str] = None) -> None:
    """Şeffaf değil; PDF gömümü için beyaz arka plan."""
    kw: Dict[str, Any] = {
        "format": "png",
        "dpi": 120,
        "facecolor": "white",
        "edgecolor": "none",
    }
    if bbox_inches:
        kw["bbox_inches"] = bbox_inches
    fig.savefig(buf, **kw)


def _chart_title_mpl_kwargs() -> Dict[str, Any]:
    """Grafik başlıkları: Arial bold (yoksa matplotlib yedekler)."""
    return {"fontname": "Arial", "fontweight": "bold", "fontsize": 11}


def _set_ax_title_arial_bold(ax: Any, title: str) -> None:
    ax.set_title(title, **_chart_title_mpl_kwargs())


def _rotate_time_x_labels_45(ax: Any) -> None:
    """Zaman ekseni etiketleri 45° eğik."""
    if plt is None:
        return
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")


def _figure_severity_bar(labels: List[str], counts: List[int]) -> Optional[io.BytesIO]:
    if plt is None:
        return None
    fig, ax = _prepare_report_figure((7.5, 4.0))
    colors_m = ["#c0392b", "#e67e22", "#f1c40f", "#27ae60"]
    ax.bar(labels, counts, color=colors_m[: len(labels)], linewidth=0, zorder=2)
    ax.set_ylabel("Olay sayısı")
    _set_ax_title_arial_bold(ax, "Önem derecesine göre dağılım")
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf)
    plt.close(fig)
    buf.seek(0)
    return buf


def _figure_hourly_line(hours: List[int], counts: List[int]) -> Optional[io.BytesIO]:
    if plt is None:
        return None
    fig, ax = _prepare_report_figure((7.5, 4.0))
    ax.fill_between(hours, counts, alpha=0.35, color="#2980b9", zorder=1)
    ax.plot(
        hours,
        counts,
        color="#2980b9",
        linewidth=2,
        marker="o",
        markersize=3.5,
        zorder=2,
    )
    ax.set_xlabel("Saat (UTC)")
    ax.set_ylabel("Olay sayısı")
    _set_ax_title_arial_bold(ax, "Günlük saatlik olay yoğunluğu")
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlim(-0.5, 23.5)
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf)
    plt.close(fig)
    buf.seek(0)
    return buf


def _figure_empty_message(msg: str) -> Optional[io.BytesIO]:
    if plt is None:
        return None
    fig, ax = _prepare_report_figure((7.5, 3.0), grid=False)
    ax.axis("off")
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12)
    fig.tight_layout()
    buf = io.BytesIO()
    _savefig_report_png(fig, buf)
    plt.close(fig)
    buf.seek(0)
    return buf


def _parse_ts_plot(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    try:
        s = str(val).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _figure_temperature_analysis(
    telemetry_rows: Sequence[Dict[str, Any]],
) -> Optional[io.BytesIO]:
    """Zaman (X) — sıcaklık (Y) çizgi grafiği."""
    if plt is None:
        return None
    series: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)
    for r in telemetry_rows:
        if str(r.get("metric", "")).lower() != METRIC_TEMPERATURE:
            continue
        ts = _parse_ts_plot(r.get("timestamp"))
        if ts is None:
            continue
        try:
            v = float(r.get("value", 0.0))
        except (TypeError, ValueError):
            continue
        label = f"{r.get('component')}/{r.get('sensor')}"
        series[label].append((ts, v))
    if not series:
        return None
    assert mdates is not None
    fig, ax = _prepare_report_figure((8.0, 4.2))
    cmap = ["#1565c0", "#6a1b9a", "#2e7d32", "#ef6c00", "#c62828", "#00838f"]
    for i, (lab, pts) in enumerate(sorted(series.items())[:10]):
        pts.sort(key=lambda p: p[0])
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(
            xs,
            ys,
            "-o",
            color=cmap[i % len(cmap)],
            label=lab[:32],
            linewidth=2,
            markersize=3.5,
            zorder=2,
        )
    ax.set_ylabel("Sicaklik (C)")
    ax.set_xlabel("Zaman")
    _set_ax_title_arial_bold(ax, "Sicaklik Analizi")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2, fontsize=7)
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _figure_cpu_temperature_series(
    times: Sequence[str],
    temps: Sequence[float],
) -> Optional[io.BytesIO]:
    """``prepare_chart_data`` çıktısı: HH:MM:SS etiketleri + sıcaklık serisi."""
    if plt is None or not times or len(times) != len(temps):
        return None
    fig, ax = _prepare_report_figure((8.0, 4.2))
    x = range(len(temps))
    ys = [float(t) for t in temps]
    ax.plot(
        x,
        ys,
        "-o",
        color="#1565c0",
        linewidth=2,
        markersize=3.5,
        zorder=2,
    )
    ax.set_ylabel("Sicaklik (C)")
    ax.set_xlabel("Zaman")
    _set_ax_title_arial_bold(ax, "CPU Sicaklik Grafigi")
    n = len(times)
    step = max(1, n // 10)
    tick_idx = list(range(0, n, step))
    if tick_idx[-1] != n - 1:
        tick_idx.append(n - 1)
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([str(times[i]) for i in tick_idx], fontsize=7)
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _figure_memory_area_chart(
    telemetry_rows: Sequence[Dict[str, Any]],
) -> Optional[io.BytesIO]:
    """RAM (memory_usage) alan grafiği."""
    if plt is None:
        return None
    pts: List[Tuple[datetime, float]] = []
    for r in telemetry_rows:
        if str(r.get("metric", "")).lower() != METRIC_MEMORY_USAGE:
            continue
        if str(r.get("component", "")).lower() != "memory":
            continue
        ts = _parse_ts_plot(r.get("timestamp"))
        if ts is None:
            continue
        try:
            v = float(r.get("value", 0.0))
        except (TypeError, ValueError):
            continue
        pts.append((ts, v))
    if len(pts) < 1:
        return None
    pts.sort(key=lambda p: p[0])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert mdates is not None
    fig, ax = _prepare_report_figure((8.0, 4.0))
    ax.fill_between(xs, ys, alpha=0.35, color="#1a237e", zorder=1)
    ax.plot(
        xs,
        ys,
        color="#1a237e",
        linewidth=2,
        marker="o",
        markersize=3.5,
        zorder=2,
    )
    ax.set_ylabel("RAM kullanimi (%)")
    ax.set_xlabel("Zaman")
    _set_ax_title_arial_bold(ax, "Bellek (RAM) kullanimi")
    ax.set_ylim(0, 105)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _collect_health_reasons_from_snapshots(
    snapshots: Sequence[Dict[str, Any]],
) -> List[str]:
    """telemetry_snapshots ``health.reasons``: önce en son anlık görüntü, yoksa tümünden birleşik."""
    if not snapshots:
        return []
    pl = snapshots[-1].get("payload") if isinstance(snapshots[-1].get("payload"), dict) else {}
    h = pl.get("health") if isinstance(pl.get("health"), dict) else {}
    last = h.get("reasons")
    if isinstance(last, list) and last:
        return [str(x).strip() for x in last if str(x).strip()]
    seen: set[str] = set()
    out: List[str] = []
    for snap in snapshots:
        pl2 = snap.get("payload") if isinstance(snap.get("payload"), dict) else {}
        h2 = pl2.get("health") if isinstance(pl2.get("health"), dict) else {}
        for r in h2.get("reasons") or []:
            t = str(r).strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _make_page_footer(font_name: str) -> Callable[..., None]:
    """ReportLab sayfa altligi: sayfa + Sistem Telemetri Raporu - 2026."""

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(COLOR_FOOTER)
        w = A4[0]
        text = f"{FOOTER_BRAND}   |   Sayfa {doc.page}"
        canvas.drawCentredString(w / 2, 0.9 * cm, text)
        canvas.restoreState()

    return footer


def _collect_recommendations(rows: Sequence[Dict[str, Any]]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for r in rows:
        pl = r.get("payload") or {}
        if isinstance(pl, dict):
            rec = str(pl.get("recommendation", "")).strip()
            if rec and rec not in seen:
                seen.add(rec)
                out.append(rec)
            code = str(pl.get("code", "")).strip()
            if code and code in _CODE_HINTS_TR:
                hint = _CODE_HINTS_TR[code]
                if hint not in seen:
                    seen.add(hint)
                    out.append(hint)
    if not rows:
        out.append("Bu gün için kayıtlı olay yok; sistem eşikleri normal görünüyor.")
    elif not out:
        out.append("Kayıtlı önerileri payload içinde kontrol edin; genel izleme yapmaya devam edin.")
    return out


def _generic_suggestions(rows: Sequence[Dict[str, Any]]) -> List[str]:
    tips: List[str] = []
    if any(int(r.get("severity", 0)) >= 40 for r in rows):
        tips.append(
            "Kritik olaylar için öncelikli müdahale planı oluşturun ve kök neden analizi yapın.",
        )
    if any(int(r.get("severity", 0)) >= 30 for r in rows):
        tips.append(
            "Yüksek önemli olaylarda ilgili metrikleri (sıcaklık, yük, disk) trend üzerinden izleyin.",
        )
    if len(rows) > 10:
        tips.append(
            "Kısa sürede çok sayıda olay: otomasyonu veya eşikleri gözden geçirmeyi düşünün.",
        )
    if not tips and rows:
        tips.append("Olayları düzenli arayla inceleyin; tekrarlayan kodları eşiklerle eşleştirin.")
    return tips


def _parse_snapshot_component_scores(payload: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Rapor ``payload`` içinden bileşen sağlık skorları (0–100)."""
    out: Dict[str, Optional[float]] = {}
    health = payload.get("health") if isinstance(payload, dict) else None
    if not isinstance(health, dict):
        return out
    cs = health.get("component_scores")
    if not isinstance(cs, dict):
        return out
    for key, _ in _COMPONENT_LABELS:
        raw = cs.get(key)
        try:
            out[key] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            out[key] = None
    return out


def _health_series_from_snapshots(
    snapshots: Sequence[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, List[Optional[float]]]]:
    """Her anlık görüntü için zaman damgası ve bileşen skorları (hizalı listeler)."""
    times: List[str] = []
    series: Dict[str, List[Optional[float]]] = {k: [] for k, _ in _COMPONENT_LABELS}
    for snap in snapshots:
        pl = snap.get("payload") if isinstance(snap.get("payload"), dict) else {}
        t = str(snap.get("generated_at", ""))[:19]
        times.append(t)
        scores = _parse_snapshot_component_scores(pl)
        for key, _ in _COMPONENT_LABELS:
            series[key].append(scores.get(key))
    return times, series


def _average_component_scores(
    series: Dict[str, List[Optional[float]]],
) -> Dict[str, float]:
    """Bileşen başına aritmetik ortalama (en az bir geçerli örnek)."""
    out: Dict[str, float] = {}
    for key, _ in _COMPONENT_LABELS:
        vals = [float(x) for x in series.get(key, []) if x is not None]
        if vals:
            out[key] = sum(vals) / len(vals)
    return out


def _figure_component_health_bar(avgs: Dict[str, float]) -> Optional[io.BytesIO]:
    """Bileşen bazlı ortalama sağlık (çubuk)."""
    if plt is None or not avgs:
        return None
    labels: List[str] = []
    values: List[float] = []
    colors_m = ["#3498db", "#9b59b6", "#1abc9c"]
    for i, (key, title) in enumerate(_COMPONENT_LABELS):
        if key in avgs:
            labels.append(title)
            values.append(avgs[key])
    if not values:
        return None
    fig, ax = _prepare_report_figure((7.5, 4.0))
    bars = ax.bar(labels, values, color=colors_m[: len(values)], linewidth=0, zorder=2)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Ortalama skor (0–100)")
    _set_ax_title_arial_bold(ax, "Son 24 saat — bileşen ortalama sağlık")
    for b, v in zip(bars, values):
        ax.text(
            b.get_x() + b.get_width() / 2.0,
            v + 2.0,
            f"{v:.1f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf)
    plt.close(fig)
    buf.seek(0)
    return buf


def _figure_component_health_timeseries(
    times: List[str],
    series_by_comp: Dict[str, List[Optional[float]]],
) -> Optional[io.BytesIO]:
    """Zaman içinde CPU / GPU / RAM skorları (çizgi)."""
    if plt is None or not times:
        return None
    has_any = any(
        any(x is not None for x in series_by_comp.get(k, [])) for k, _ in _COMPONENT_LABELS
    )
    if not has_any:
        return None
    fig, ax = _prepare_report_figure((7.5, 4.2))
    x = range(len(times))
    styles = [
        ("#3498db", "o", 3.5),
        ("#9b59b6", "s", 3.5),
        ("#1abc9c", "^", 3.5),
    ]
    for (key, title), (col, mkr, ms) in zip(_COMPONENT_LABELS, styles):
        ys: List[float] = []
        for v in series_by_comp.get(key, []):
            if v is None:
                ys.append(float("nan"))
            else:
                ys.append(float(v))
        if any(y == y for y in ys):
            ax.plot(
                x,
                ys,
                color=col,
                label=title,
                linewidth=2,
                marker=mkr,
                markersize=ms,
                zorder=2,
            )
    ax.set_ylim(0, 105)
    ax.set_ylabel("Skor")
    _set_ax_title_arial_bold(ax, "Son 24 saat — bileşen sağlığı (anlık görüntüler)")
    step = max(1, len(times) // 8)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([times[i] for i in x[::step]], fontsize=7)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    _rotate_time_x_labels_45(ax)
    buf = io.BytesIO()
    _savefig_report_png(fig, buf)
    plt.close(fig)
    buf.seek(0)
    return buf


def _latest_critical_incident(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Önem 40 (kritik) en son kayıt."""
    critics: List[Dict[str, Any]] = []
    for r in rows:
        try:
            if int(r.get("severity", 0)) >= 40:
                critics.append(r)
        except (TypeError, ValueError):
            continue
    if not critics:
        return None
    return max(critics, key=lambda r: str(r.get("created_at", "")))


def _format_details_for_box(pl: Dict[str, Any]) -> str:
    det = pl.get("details")
    if isinstance(det, dict) and det:
        parts = [f"{k}: {v}" for k, v in list(det.items())[:8]]
        return " | ".join(parts)
    return ""


def _build_critical_alarm_box(
    row: Dict[str, Any],
    font: str,
    *,
    box_width: float,
) -> Table:
    """Kırmızı arka planlı kritik uyarı kutusu (en üstte)."""
    pl = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    title = escape(str(row.get("title", "")))
    code = escape(str(pl.get("code", "")))
    rec = escape(str(pl.get("recommendation", "")))
    when = escape(str(row.get("created_at", ""))[:22])
    comp = escape(str(pl.get("component", "")))
    details_txt = escape(_format_details_for_box(pl))

    hdr_style = ParagraphStyle(
        name="CritHdr",
        fontName=font,
        fontSize=11,
        leading=14,
        textColor=colors.white,
    )
    txt_style = ParagraphStyle(
        name="CritTxt",
        fontName=font,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#ffeeee"),
    )

    blocks: List[Any] = [
        Paragraph("<b>KRİTİK UYARI</b>", hdr_style),
        Spacer(1, 0.15 * cm),
        Paragraph(f"<b>Zaman:</b> {when}", txt_style),
        Paragraph(f"<b>Başlık:</b> {title}", txt_style),
        Paragraph(f"<b>Kod:</b> {code}", txt_style),
        Paragraph(f"<b>Bileşen:</b> {comp}", txt_style),
    ]
    if details_txt:
        blocks.append(Paragraph(f"<b>Detay:</b> {details_txt}", txt_style))
    if rec:
        blocks.append(Spacer(1, 0.1 * cm))
        blocks.append(Paragraph(f"<b>Öneri:</b> {rec}", txt_style))

    inner = Table([[b] for b in blocks], colWidths=[box_width - 0.6 * cm])
    inner.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ],
        ),
    )
    outer = Table([[inner]], colWidths=[box_width])
    outer.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#b71c1c")),
                ("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor("#7f0000")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ],
        ),
    )
    return outer


class PdfExporter:
    """SQLite telemetri / olaylar; ReportLab + matplotlib PDF."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.PdfExporter")

    def export_system_telemetry_report(
        self,
        output_path: Path,
        *,
        db_path: Path,
    ) -> Path:
        """
        Son 24 saat: ``telemetry`` + ``incidents`` + ``telemetry_snapshots`` (health.reasons).

        Grafikler: Sicaklik Analizi (cizgi), RAM alan grafigi; Sistem Tavsiyeleri;
        lacivert basliklar, kritik olaylar kirmizi; alt bilgi sayfa + 'Sistem Telemetri Raporu - 2026'.
        """
        if pdfmetrics is None or plt is None:
            raise RuntimeError(
                "PDF icin reportlab ve matplotlib gerekli: pip install reportlab matplotlib",
            )
        start_iso, end_iso = _utc_last_24h_bounds()
        db = Database(db_path)
        repo = TelemetryRepository(db)
        tel_rows = repo.list_telemetry_between(start_iso, end_iso)
        incidents = repo.list_incidents_between(start_iso, end_iso)
        snapshots = repo.list_telemetry_snapshots_between(start_iso, end_iso)
        reasons = _collect_health_reasons_from_snapshots(snapshots)

        font = _register_unicode_font()
        footer_fn = _make_page_footer(font)

        styles = getSampleStyleSheet()
        h_title = ParagraphStyle(
            name="SysTitle",
            parent=styles["Heading1"],
            fontName=font,
            fontSize=18,
            textColor=COLOR_NAVY,
            alignment=TA_CENTER,
            spaceAfter=14,
        )
        h_sec = ParagraphStyle(
            name="SysSec",
            parent=styles["Heading2"],
            fontName=font,
            fontSize=13,
            textColor=COLOR_NAVY,
            spaceBefore=10,
            spaceAfter=8,
        )
        body = ParagraphStyle(
            name="SysBody",
            parent=styles["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
        )
        body_red = ParagraphStyle(
            name="SysBodyRed",
            parent=body,
            textColor=COLOR_CRITICAL_TEXT,
        )
        tbl_st = ParagraphStyle(
            name="SysTbl",
            parent=body,
            fontSize=8,
            leading=10,
        )

        story: List[Any] = []
        story.append(
            Paragraph(
                "<b>Sistem Telemetri Ozeti</b><br/>Son 24 saat (UTC)",
                h_title,
            ),
        )
        story.append(
            Paragraph(
                f"Aralik: {escape(start_iso[:22])} - {escape(end_iso[:22])}<br/>"
                f"Veritabani: {escape(str(db_path.name))}",
                body,
            ),
        )
        story.append(Spacer(1, 0.45 * cm))

        story.append(Paragraph("Sicaklik Analizi", h_sec))
        buf_t = _figure_temperature_analysis(tel_rows)
        if buf_t:
            story.append(RLImage(buf_t, width=15.5 * cm, height=8 * cm))
        else:
            story.append(
                Paragraph(
                    "Son 24 saatte sicaklik (temperature) telemetrisi kaydi yok.",
                    body,
                ),
            )
        story.append(Spacer(1, 0.35 * cm))

        story.append(Paragraph("Bellek (RAM) kullanimi", h_sec))
        buf_m = _figure_memory_area_chart(tel_rows)
        if buf_m:
            story.append(RLImage(buf_m, width=15.5 * cm, height=7.5 * cm))
        else:
            story.append(
                Paragraph(
                    "Son 24 saatte bellek (memory_usage) telemetrisi kaydi yok.",
                    body,
                ),
            )
        story.append(Spacer(1, 0.35 * cm))

        story.append(Paragraph("Olaylar (incidents)", h_sec))
        crit_rows = [r for r in incidents if int(r.get("severity", 0)) >= 40]
        if crit_rows:
            story.append(
                Paragraph(
                    f"<b>Kritik kayitlar ({len(crit_rows)}):</b>",
                    body_red,
                ),
            )
            for r in crit_rows[:20]:
                pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
                line = (
                    f"{str(r.get('created_at', ''))[:22]} | "
                    f"{r.get('title', '')} | {pl.get('code', '')}"
                )
                story.append(Paragraph(escape(line), body_red))
            story.append(Spacer(1, 0.25 * cm))

        inc_header = ["Zaman", "Oncelik", "Baslik", "Kod"]
        inc_flow: List[List[Any]] = [
            [Paragraph(escape(c), tbl_st) for c in inc_header],
        ]
        for r in incidents[:40]:
            pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            sev = int(r.get("severity", 0))
            lbl = _severity_label(sev)
            row_cells = [
                escape(str(r.get("created_at", ""))[:19]),
                lbl,
                escape(str(r.get("title", ""))[:45]),
                escape(str(pl.get("code", ""))[:18]),
            ]
            style = body_red if sev >= 40 else tbl_st
            inc_flow.append([Paragraph(c, style) for c in row_cells])
        if len(incidents) > 40:
            inc_flow.append(
                [Paragraph(escape("..."), tbl_st), Paragraph("", tbl_st), Paragraph("", tbl_st), Paragraph("", tbl_st)],
            )
        if len(incidents) == 0:
            story.append(Paragraph("Son 24 saatte olay kaydi yok.", body))
        else:
            itbl = Table(
                inc_flow,
                colWidths=[3.6 * cm, 2.0 * cm, 5.8 * cm, 3.2 * cm],
            )
            itbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaf6")),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ],
                ),
            )
            story.append(itbl)

        story.append(Spacer(1, 0.45 * cm))
        story.append(Paragraph("Sistem Tavsiyeleri", h_sec))
        if reasons:
            for line in reasons:
                story.append(Paragraph(f"&bull; {escape(line)}", body))
        else:
            story.append(
                Paragraph(
                    "Health Score &lsquo;reasons&rsquo; listesi yok: "
                    "telemetry_snapshots icinde son raporlar bekleniyor.",
                    body,
                ),
            )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=1.8 * cm,
            rightMargin=1.8 * cm,
            topMargin=1.5 * cm,
            bottomMargin=2.5 * cm,
        )
        try:
            doc.build(story, onFirstPage=footer_fn, onLaterPages=footer_fn)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Sistem PDF hatasi: %s", exc)
            raise
        self._logger.info("Sistem telemetri PDF: %s", output_path)
        return output_path

    def export_daily_incident_report(
        self,
        output_path: Path,
        *,
        db_path: Path,
        report_date: Optional[date] = None,
    ) -> Path:
        """
        Belirtilen gün (UTC) için olayları çeker, grafikler ve önerilerle PDF üretir.

        Dönüş: yazılan dosya yolu.
        """
        if pdfmetrics is None or plt is None:
            raise RuntimeError(
                "PDF için reportlab ve matplotlib gerekli: pip install reportlab matplotlib",
            )
        report_date = report_date or datetime.now(timezone.utc).date()
        start_iso, end_iso = _utc_day_bounds(report_date)

        db = Database(db_path)
        repo = TelemetryRepository(db)
        rows = repo.list_incidents_between(start_iso, end_iso)
        health_start, health_end = _utc_last_24h_bounds()
        snapshots_24h = repo.list_telemetry_snapshots_between(health_start, health_end)
        h_times, h_series = _health_series_from_snapshots(snapshots_24h)
        h_avgs = _average_component_scores(h_series)

        font = _register_unicode_font()
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            name="Title",
            parent=styles["Heading1"],
            fontName=font,
            fontSize=14,
            textColor=COLOR_NAVY,
            alignment=TA_CENTER,
            spaceAfter=12,
        )
        body_style = ParagraphStyle(
            name="Body",
            parent=styles["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
        )
        table_cell_style = ParagraphStyle(
            name="Tbl",
            parent=body_style,
            fontSize=8,
            leading=10,
        )
        h2_style = ParagraphStyle(
            name="H2",
            parent=styles["Heading2"],
            fontName=font,
            fontSize=11,
            textColor=COLOR_NAVY,
            spaceBefore=10,
            spaceAfter=6,
        )
        footer_fn = _make_page_footer(font)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            leftMargin=1.8 * cm,
            rightMargin=1.8 * cm,
            topMargin=1.5 * cm,
            bottomMargin=2.5 * cm,
        )
        story: List[Any] = []

        crit = _latest_critical_incident(rows)
        if crit is not None:
            story.append(
                _build_critical_alarm_box(
                    crit,
                    font,
                    box_width=A4[0] - 3.6 * cm,
                ),
            )
            story.append(Spacer(1, 0.45 * cm))

        story.append(
            Paragraph(
                f"<b>Günlük olay özeti (incidents)</b><br/>"
                f"Tarih (UTC): {report_date.isoformat()}",
                title_style,
            ),
        )
        story.append(
            Paragraph(
                f"Toplam olay: <b>{len(rows)}</b> — SQLite: {db_path.name}",
                body_style,
            ),
        )
        story.append(Spacer(1, 0.35 * cm))

        story.append(
            Paragraph(
                "<b>Bileşen sağlığı (son 24 saat, UTC)</b><br/>"
                f"Kaynak: telemetry_snapshots, pencere: son 24 saat "
                f"({health_start[:16]} … {health_end[:16]}).",
                body_style,
            ),
        )
        story.append(Spacer(1, 0.25 * cm))
        if h_avgs:
            buf_h1 = _figure_component_health_bar(h_avgs)
            if buf_h1:
                story.append(RLImage(buf_h1, width=14 * cm, height=7.5 * cm))
            story.append(Spacer(1, 0.25 * cm))
        if len(h_times) > 1:
            buf_h2 = _figure_component_health_timeseries(h_times, h_series)
            if buf_h2:
                story.append(RLImage(buf_h2, width=14 * cm, height=8 * cm))
        elif not h_avgs:
            buf_empty = _figure_empty_message(
                "Son 24 saatte telemetry_snapshots içinde sağlık verisi yok.",
            )
            if buf_empty:
                story.append(RLImage(buf_empty, width=14 * cm, height=3.5 * cm))
        story.append(Spacer(1, 0.4 * cm))

        labels, counts = _severity_counts(rows)
        hours, hcnt = _hourly_counts(rows)

        if sum(counts) == 0:
            buf = _figure_empty_message("Bu gün için kayıtlı olay yok.")
            if buf:
                story.append(RLImage(buf, width=14 * cm, height=4 * cm))
        else:
            buf1 = _figure_severity_bar(labels, counts)
            if buf1:
                story.append(RLImage(buf1, width=14 * cm, height=7.5 * cm))
            story.append(Spacer(1, 0.3 * cm))
            buf2 = _figure_hourly_line(hours, hcnt)
            if buf2:
                story.append(RLImage(buf2, width=14 * cm, height=7.5 * cm))

        story.append(Paragraph("Olay tablosu (özet)", h2_style))
        table_data: List[List[str]] = [
            ["Zaman (created_at)", "Önem", "Başlık", "Kod"],
        ]
        for r in rows[:50]:
            pl = r.get("payload") or {}
            code = ""
            if isinstance(pl, dict):
                code = str(pl.get("code", ""))
            table_data.append(
                [
                    str(r.get("created_at", ""))[:19],
                    _severity_label(int(r.get("severity", 0))),
                    str(r.get("title", ""))[:55],
                    code[:24],
                ],
            )
        if len(rows) > 50:
            table_data.append(["...", f"(+{len(rows) - 50} kayıt daha)", "", ""])

        table_flow: List[List[Any]] = [
            [Paragraph(escape(c), table_cell_style) for c in table_data[0]],
        ]
        for row in table_data[1:]:
            table_flow.append([Paragraph(escape(c), table_cell_style) for c in row])

        tbl = Table(table_flow, colWidths=[3.8 * cm, 2.2 * cm, 5.5 * cm, 3.5 * cm])
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ],
            ),
        )
        story.append(tbl)
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph("Çözüm ve öneriler (payload + kod ipuçları)", h2_style))
        for line in _collect_recommendations(rows):
            story.append(Paragraph(f"• {escape(line)}", body_style))
        for line in _generic_suggestions(rows):
            story.append(Paragraph(f"• {escape(line)}", body_style))

        try:
            doc.build(story, onFirstPage=footer_fn, onLaterPages=footer_fn)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("PDF oluşturma hatası: %s", exc)
            raise
        self._logger.info("Günlük PDF yazıldı: %s", output_path)
        return output_path

    def export_text_report(self, text: str, path: Path) -> None:
        """Geriye uyumluluk: düz metin raporu (PDF değil)."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fallback = path.with_suffix(".txt") if path.suffix.lower() == ".pdf" else path
            with fallback.open("w", encoding="utf-8") as f:
                f.write(text)
            if fallback.suffix.lower() == ".txt" and path.suffix.lower() == ".pdf":
                self._logger.info("Metin raporu: %s", fallback)
        except OSError as exc:
            self._logger.exception("Metin yazma hatası: %s", exc)
            raise

    def export_from_dict(self, report: Dict[str, Any], path: Path) -> None:
        """Basit dict → metin (hızlı dışa aktarma)."""
        lines = [f"{k}: {v}" for k, v in report.items()]
        self.export_text_report("\n".join(lines), path)


# ---------------------------------------------------------------------------
# FPDF2 iskeleti: SystemReport + FpdfDailyReportBuilder (core.reporting.ReportBuilder ile karismaz)
# ---------------------------------------------------------------------------

# Tema: ana başlıklar lacivert; kritik soft kırmızı; uyarı sarı ton; iyi durum zümrüt yeşili
FPDF_COLOR_NAVY = (26, 35, 126)  # #1A237E
FPDF_COLOR_CRITICAL_BG = (255, 235, 238)  # #FFEBEE
FPDF_COLOR_CRITICAL_TEXT = (183, 28, 28)  # #B71C1C
FPDF_COLOR_WARNING_BG = (255, 250, 200)
FPDF_COLOR_WARNING_TEXT = (109, 76, 0)
FPDF_COLOR_OK = (46, 125, 50)  # #2E7D32

# Zebra: normal satırlar (tek indeks) hafif gri
FPDF_ZEBRA_GRAY = (245, 245, 245)


def fpdf_dated_report_filename(now: Optional[datetime] = None) -> str:
    """Örn. ``REPORT_2026_04_02.pdf`` (bugünün tarihi)."""
    t = now or datetime.now()
    return f"REPORT_{t.strftime('%Y_%m_%d')}.pdf"


class SystemReport(FPDF):
    """
    Gunluk saglik PDF cercevesi — baslik / altbilgi.

    Önce Arial (Bold / Regular), yoksa DejaVu, yoksa Helvetica.
    """

    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.font_family: str = "Helvetica"
        self.set_auto_page_break(auto=True, margin=22)
        if not self._try_load_arial():
            self._try_load_dejavu()

    def _try_load_arial(self) -> bool:
        if FPDF is None:
            return False
        pairs: List[Tuple[Path, Path]] = []
        if os.name == "nt":
            wd = Path(os.environ.get("WINDIR", r"C:\Windows"))
            pairs.append((wd / "Fonts" / "arial.ttf", wd / "Fonts" / "arialbd.ttf"))
            pairs.append((wd / "Fonts" / "ARIAL.TTF", wd / "Fonts" / "ARIALBD.TTF"))
        pairs.extend(
            [
                (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf")),
                (
                    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
                ),
            ],
        )
        for reg, bd in pairs:
            if not reg.is_file() or not bd.is_file():
                continue
            try:
                self.add_font("Arial", "", str(reg))
                self.add_font("Arial", "B", str(bd))
                self.font_family = "Arial"
                return True
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("FPDF Arial yuklenemedi (%s): %s", reg, exc)
        return False

    def _try_load_dejavu(self) -> None:
        if matplotlib is None or FPDF is None:
            return
        try:
            base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
            reg = base / "DejaVuSans.ttf"
            bold = base / "DejaVuSans-Bold.ttf"
            if reg.is_file():
                self.add_font("DejaVu", "", str(reg))
                if bold.is_file():
                    self.add_font("DejaVu", "B", str(bold))
                self.font_family = "DejaVu"
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("FPDF DejaVu yuklenemedi: %s", exc)

    def header(self) -> None:
        self.set_text_color(*FPDF_COLOR_NAVY)
        self.set_font(self.font_family, "B", 15)
        self.cell(0, 10, "Sistem Telemetri - Gunluk Saglik Raporu", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_text_color(69, 90, 100)
        self.set_font(self.font_family, "", 10)
        self.cell(
            0,
            10,
            f'Rapor Zamani: {datetime.now().strftime("%Y-%m-%d %H:%M")}',
            new_x="LMARGIN",
            new_y="NEXT",
            align="R",
        )
        self.set_text_color(0, 0, 0)
        self.ln(8)

    def footer(self) -> None:
        self.set_draw_color(200, 200, 200)
        self.set_line_width(0.3)
        self.line(self.l_margin, -22, self.w - self.r_margin, -22)
        self.set_y(-18)
        self.set_font(self.font_family, "", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 4, f"Sistem Telemetri Raporu - 2026  |  Sayfa {self.page_no()}", align="C")
        self.set_y(-13)
        self.set_font(self.font_family, "", 6)
        self.set_text_color(175, 175, 175)
        self.cell(0, 3, "System Telemetry AI-Powered Report", align="R")
        self.set_text_color(0, 0, 0)


class FpdfDailyReportBuilder:
    """
    ``TelemetryRepository`` + FPDF: sicaklik grafigi icin ``telemetry`` tablosundan
    son N kayit (varsayilan 50), olaylar ve anlik goruntuler son 24 saat.
    """

    def __init__(self, db_repository: TelemetryRepository) -> None:
        self.repo = db_repository
        self.pdf: Optional[SystemReport] = None
        self._manual = False
        self._logger = get_logger(f"{__name__}.FpdfDailyReportBuilder")

    def set_status_color(self, status: str) -> None:
        """
        Satır / blok arka planı ve metin rengi: ``critical`` | ``warning`` | ``ok`` | ``normal``.

        Kritik: #FFEBEE zemin, #B71C1C yazı; uyarı: sarı ton zemin; iyi: zümrüt yazı (beyaz zemin).
        """
        assert self.pdf is not None
        s = (status or "normal").lower()
        if s == "critical":
            self.pdf.set_fill_color(*FPDF_COLOR_CRITICAL_BG)
            self.pdf.set_text_color(*FPDF_COLOR_CRITICAL_TEXT)
        elif s == "warning":
            self.pdf.set_fill_color(*FPDF_COLOR_WARNING_BG)
            self.pdf.set_text_color(*FPDF_COLOR_WARNING_TEXT)
        elif s == "ok":
            self.pdf.set_fill_color(255, 255, 255)
            self.pdf.set_text_color(*FPDF_COLOR_OK)
        else:
            self.pdf.set_fill_color(255, 255, 255)
            self.pdf.set_text_color(0, 0, 0)

    def set_heading_style(self) -> None:
        """Ana bölüm başlıkları — lacivert (#1A237E)."""
        assert self.pdf is not None
        self.pdf.set_text_color(*FPDF_COLOR_NAVY)

    def reset_typography(self) -> None:
        """Gövde metni ve zemin varsayılanları."""
        assert self.pdf is not None
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.set_fill_color(255, 255, 255)

    def _apply_zebra_or_status_row_fill(self, row_st: str, zebra: bool) -> None:
        """Kritik/uyarı: tema renkleri; normal: zebra (beyaz / çok açık gri)."""
        assert self.pdf is not None
        if row_st == "critical":
            self.set_status_color("critical")
        elif row_st == "warning":
            self.set_status_color("warning")
        else:
            if zebra:
                self.pdf.set_fill_color(*FPDF_ZEBRA_GRAY)
            else:
                self.pdf.set_fill_color(255, 255, 255)
            self.pdf.set_text_color(0, 0, 0)

    def _draw_health_score_progress_bar(self, score: float) -> None:
        """0–100 skor; 80+ yeşil, 50–80 turuncu, altı kırmızı dolgu."""
        pdf = self.pdf
        assert pdf is not None
        lm = pdf.l_margin
        w_track = pdf.w - lm - pdf.r_margin
        h_bar = 4.5
        x0 = lm
        y0 = pdf.get_y()
        pct = max(0.0, min(100.0, score)) / 100.0
        w_fill = w_track * pct
        if score >= 80.0:
            rgb = (46, 125, 50)
        elif score >= 50.0:
            rgb = (245, 124, 0)
        else:
            rgb = (183, 28, 28)
        pdf.set_fill_color(236, 236, 236)
        pdf.rect(x0, y0, w_track, h_bar, "F")
        pdf.set_fill_color(*rgb)
        if w_fill > 0.05:
            pdf.rect(x0, y0, w_fill, h_bar, "F")
        pdf.set_draw_color(190, 190, 190)
        pdf.rect(x0, y0, w_track, h_bar, "D")
        pdf.set_xy(lm, y0 + h_bar + 1.5)

    def _draw_severity_badge(
        self,
        x0: float,
        y0: float,
        col_w: float,
        row_h: float,
        row_st: str,
    ) -> None:
        """
        Öncelik sütununda durum rozeti: ``rect(..., 'F')`` ile dolu dikdörtgen, üzerine beyaz yazı.
        """
        assert self.pdf is not None
        pdf = self.pdf
        ff = pdf.font_family
        if row_st == "critical":
            bg = FPDF_COLOR_CRITICAL_TEXT
        elif row_st == "warning":
            bg = (245, 124, 0)
        else:
            bg = FPDF_COLOR_OK
        caption = _badge_caption(row_st)
        pdf.set_font(ff, "", 6.5)
        pad = 0.55
        tw = pdf.get_string_width(caption)
        bw = min(max(tw + 2 * pad, 12.0), col_w - 1.0)
        bh = min(4.0, row_h - 1.0)
        bx = x0 + (col_w - bw) / 2
        by = y0 + (row_h - bh) / 2
        pdf.set_fill_color(*bg)
        pdf.rect(bx, by, bw, bh, "F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(bx, by)
        pdf.cell(bw, bh, caption, align="C", border=0)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font(ff, "", 8)

    def _semantic_score_status(self, score: float) -> str:
        if score > 75.0:
            return "ok"
        if score >= 50.0:
            return "warning"
        return "critical"

    def create_thermal_chart(
        self,
        data: Union[Sequence[Dict[str, Any]], Dict[str, Any]],
    ) -> Optional[io.BytesIO]:
        """
        Sicaklik PNG buffer (matplotlib ``fig.savefig`` doğrudan ``io.BytesIO``; ara PNG dosyası yok).

        - Telemetri satırları: çok serili ``_figure_temperature_analysis``.
        - Dict: ``{"timestamps"|"times", "temps"|"values"}`` (``prepare_chart_data`` ile uyumlu).
        """
        if isinstance(data, dict):
            times = data.get("timestamps") or data.get("times")
            raw_temps = data.get("temps") or data.get("values")
            if not times or raw_temps is None or len(times) != len(raw_temps):
                return None
            temps_f: List[float] = []
            for t in raw_temps:
                try:
                    temps_f.append(float(t))
                except (TypeError, ValueError):
                    return None
            return _figure_cpu_temperature_series(list(times), temps_f)
        return _figure_temperature_analysis(data)

    def start_document(self) -> None:
        """Orkestrasyon: boş ilk sayfa + başlık (FPDF ``header``). ``build_report`` / ``finalize_report`` öncesi."""
        if FPDF is None:
            raise RuntimeError("fpdf2 gerekli: pip install fpdf2")
        self.pdf = SystemReport()
        self.pdf.add_page()
        self._manual = True

    def _write_pdf(self, output_path: Path) -> Path:
        assert self.pdf is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.pdf.output(str(output_path))
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("FPDF yazma hatasi: %s", exc)
            raise
        self._logger.info("FPDF rapor: %s", output_path)
        return output_path

    def finalize_report(self, output_path: str | Path) -> Path:
        """Açık PDF'i diske yazar (``start_document`` + bölümler sonrası)."""
        if FPDF is None:
            raise RuntimeError("fpdf2 gerekli: pip install fpdf2")
        if self.pdf is None:
            raise RuntimeError("PDF bos: once start_document() veya tam build_report()")
        return self._write_pdf(Path(output_path))

    def add_hardware_inventory_section(self, data: Optional[Dict[str, str]] = None) -> None:
        """Gri çerçeveli donanım tablosu (Genel Özet üstü, ilk sayfa)."""
        from core.reporting.hardware_inventory import collect_hardware_inventory

        inv = data if data is not None else collect_hardware_inventory()
        assert self.pdf is not None
        pdf = self.pdf
        ff = pdf.font_family
        self.set_heading_style()
        pdf.set_font(ff, "B", 11)
        pdf.cell(0, 8, "Donanim Envanteri", new_x="LMARGIN", new_y="NEXT")
        self.reset_typography()
        lm = pdf.l_margin
        w = pdf.w - lm - pdf.r_margin
        col_w = 42.0
        val_w = w - col_w
        pdf.set_draw_color(130, 130, 130)
        pdf.set_line_width(0.35)
        pdf.set_text_color(45, 45, 45)
        rows = [
            ("Islemci (CPU)", inv.get("cpu", "—")),
            ("Ekran karti (GPU)", inv.get("gpu", "—")),
            ("Toplam RAM", inv.get("ram", "—")),
            ("Isletim sistemi", inv.get("os", "—")),
        ]
        h = 7.0
        for label, raw in rows:
            val = str(raw).replace("\n", " ").strip()
            if len(val) > 110:
                val = val[:107] + "..."
            pdf.set_font(ff, "B", 8)
            pdf.cell(col_w, h, label, border=1)
            pdf.set_font(ff, "", 8)
            pdf.cell(val_w, h, val, border=1, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.2)
        self.reset_typography()
        pdf.ln(2)

    def add_chart(
        self,
        img_buf: io.BytesIO,
        *,
        title: str = "",
        new_page: bool = True,
    ) -> None:
        """
        Matplotlib çıktısını diske yazmadan ``io.BytesIO`` ile PDF'e gömer (``image`` ham bayt okur).
        """
        assert self.pdf is not None
        ff = self.pdf.font_family
        if new_page:
            self.pdf.add_page()
        if title:
            self.set_heading_style()
            self.pdf.set_font(ff, "B", 12)
            self.pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
            self.reset_typography()
        img_buf.seek(0)
        self.pdf.image(img_buf, x=10, w=190)

    def add_summary_section(self, health_data: Dict[str, Any], *, new_page: bool = True) -> None:
        """Genel saglik puani ve kisa ozet."""
        assert self.pdf is not None
        ff = self.pdf.font_family
        if new_page:
            self.pdf.add_page()
        self.set_heading_style()
        self.pdf.set_font(ff, "B", 12)
        self.pdf.cell(0, 10, "1. Genel Sistem Ozeti", new_x="LMARGIN", new_y="NEXT")
        self.reset_typography()
        self.pdf.set_font(ff, "", 10)
        pending = bool(health_data.get("pending"))
        raw_score = health_data.get("score")
        if pending or raw_score is None:
            self.pdf.cell(
                0,
                6,
                "Genel Saglik Puani: Hesaplanıyor (veri / analiz bekleniyor)",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            self.pdf.ln(1)
        else:
            score = float(raw_score)
            self.pdf.cell(0, 6, f"Genel Saglik Puani: {score:.1f} / 100", new_x="LMARGIN", new_y="NEXT")
            self._draw_health_score_progress_bar(score)
            self.pdf.ln(1)
        reasons = health_data.get("reasons")
        if isinstance(reasons, list) and reasons:
            self.pdf.set_font(ff, "", 9)
            self.pdf.multi_cell(0, 6, "Sebepler: " + " | ".join(str(r) for r in reasons[:5]))

    def add_incident_log(self, incidents: Sequence[Dict[str, Any]], *, new_page: bool = False) -> None:
        """Olay tablosu: ``details`` JSON string ise parse; kritik satırda açık kırmızı zemin."""
        assert self.pdf is not None
        ff = self.pdf.font_family
        if new_page:
            self.pdf.add_page()
        self.pdf.ln(6)
        self.set_heading_style()
        self.pdf.set_font(ff, "B", 12)
        self.pdf.cell(0, 10, "2. Olay Gunlugu (incidents)", new_x="LMARGIN", new_y="NEXT")
        self.reset_typography()
        self.set_heading_style()
        self.pdf.set_font(ff, "B", 9)
        self.pdf.cell(40, 7, "Zaman", border=1)
        self.pdf.cell(40, 7, "Bilesen", border=1)
        self.pdf.cell(25, 7, "Oncelik", border=1)
        self.pdf.cell(85, 7, "Baslik / kod", border=1, new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_font(ff, "B", 8)
        self.pdf.cell(190, 5, "Detay (JSON / metin)", border=1, new_x="LMARGIN", new_y="NEXT")
        self.reset_typography()
        self.pdf.set_font(ff, "", 8)
        for i, inc in enumerate(incidents[:35]):
            pl = inc.get("payload") if isinstance(inc.get("payload"), dict) else {}
            ts = str(inc.get("created_at", ""))[:19]
            comp = str(inc.get("component") or pl.get("component", "-"))[:40]
            title = str(inc.get("title", ""))[:45]
            code = str(pl.get("code", ""))[:20]
            row_txt = f"{title} [{code}]" if code else title
            row_st = _incident_row_status(inc)
            zebra = i % 2 == 1
            self._apply_zebra_or_status_row_fill(row_st, zebra)
            self.pdf.cell(40, 6, ts, border=1, fill=True)
            self.pdf.cell(40, 6, comp, border=1, fill=True)
            x_pri = self.pdf.get_x()
            y_pri = self.pdf.get_y()
            pri_w, row_h = 25.0, 6.0
            self.pdf.cell(pri_w, row_h, "", border=1, fill=True)
            self.pdf.set_xy(x_pri, y_pri)
            self._draw_severity_badge(x_pri, y_pri, pri_w, row_h, row_st)
            self.pdf.set_xy(x_pri + pri_w, y_pri)
            self.pdf.cell(85, 6, row_txt[:80], border=1, new_x="LMARGIN", new_y="NEXT", fill=True)
            parsed = _incident_parse_details(inc.get("details"), pl)
            det_txt = _incident_details_text(parsed)
            self._apply_zebra_or_status_row_fill(row_st, zebra)
            self.pdf.multi_cell(190, 4, det_txt, border=1, fill=True)
        self.reset_typography()

    def build_report(self, output_path: str | Path | None = None) -> Path:
        """
        Tam otomatik: SQLite ``telemetry`` son 50 kayit + olaylar + snapshots.

        Orkestrasyon modunda (``start_document`` sonrasi) yalnizca diske yazar.
        ``output_path`` verilmezse ``reports/REPORT_YYYY_MM_DD.pdf``.
        """
        if FPDF is None:
            raise RuntimeError("fpdf2 gerekli: pip install fpdf2")
        if output_path is None:
            output_path = Path("reports") / fpdf_dated_report_filename()
        else:
            output_path = Path(output_path)
        if getattr(self, "_manual", False) and self.pdf is not None:
            return self._write_pdf(output_path)

        self._manual = False
        start_iso, end_iso = _utc_last_24h_bounds()
        # Ozet sorgu: sadece grafik icin gerekli kolonlar (get_recent_telemetry)
        last_50 = self.repo.get_recent_telemetry(50)
        tel_rows: List[Dict[str, Any]] = list(reversed(last_50))

        incidents = self.repo.get_daily_incidents()
        snapshots = self.repo.list_telemetry_snapshots_between(start_iso, end_iso)
        health: Dict[str, Any] = {}
        if snapshots:
            pl = snapshots[-1].get("payload") if isinstance(snapshots[-1].get("payload"), dict) else {}
            h = pl.get("health")
            if isinstance(h, dict):
                health = dict(h)

        self.pdf = SystemReport()
        self.pdf.add_page()
        self.add_hardware_inventory_section()
        ff = self.pdf.font_family
        self.set_heading_style()
        self.pdf.set_font(ff, "B", 12)
        self.pdf.cell(0, 10, "Sicaklik Trend Analizi (son 50 kayit)", new_x="LMARGIN", new_y="NEXT")
        self.reset_typography()
        img_buf = self.create_thermal_chart(tel_rows)
        if img_buf:
            img_buf.seek(0)
            self.pdf.image(img_buf, x=10, w=190)
        else:
            self.pdf.set_font(ff, "", 10)
            self.pdf.multi_cell(
                0,
                6,
                "Son 50 kayitta sicaklik (temperature) olcumu yok veya grafik uretilemedi.",
            )

        self.add_summary_section(
            health if health else dict(PENDING_HEALTH_SNAPSHOT),
            new_page=False,
        )
        crit = [r for r in incidents if _incident_is_critical(r)]
        self.add_incident_log(crit if crit else incidents)

        return self._write_pdf(output_path)


PdfReportBuilder = FpdfDailyReportBuilder


def build_fpdf_daily_report(output_path: Path, db_path: Path) -> Path:
    """Kisa yol: SQLite dosyasi + FPDF gunluk rapor."""
    db = Database(db_path)
    repo = TelemetryRepository(db)
    return FpdfDailyReportBuilder(repo).build_report(output_path)
