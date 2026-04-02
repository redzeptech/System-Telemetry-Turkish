"""Metin tabanlı özet panel."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

# Konsol ANSI (Windows 10+ VT)
_RESET = "\033[0m"
_DIM = "\033[2m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_GREEN = "\033[92m"


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            kernel32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def _use_color() -> bool:
    return sys.stdout.isatty()


def _wrap_status(text: str, status: Optional[str]) -> str:
    if not _use_color():
        return text
    s = str(status or "").lower()
    if s == "critical":
        return f"{_RED}{text}{_RESET}"
    if s == "warning":
        return f"{_YELLOW}{text}{_RESET}"
    if s in ("normal", "ok"):
        return f"{_GREEN}{text}{_RESET}"
    return text


def _wrap_health_line(text: str, health: Any) -> str:
    if not _use_color() or not isinstance(health, dict):
        return text
    try:
        sc = float(health.get("score", 100.0))
    except (TypeError, ValueError):
        return text
    if sc < 70.0:
        return f"{_RED}{text}{_RESET}"
    if sc < 95.0:
        return f"{_YELLOW}{text}{_RESET}"
    return f"{_GREEN}{text}{_RESET}"


class DashboardCli:
    """Konsola telemetri özetini yazdırır."""

    def __init__(self) -> None:
        self._logger = get_logger(f"{__name__}.DashboardCli")
        _enable_windows_ansi()

    def clear_screen(self) -> None:
        """Konsolu temizler (Windows / POSIX)."""
        try:
            if sys.platform == "win32":
                os.system("cls")  # noqa: S605,S607
            else:
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("Ekran temizlenemedi: %s", exc)

    def render(self, report: Dict[str, Any], width: int = 64) -> str:
        """Rapor dict'inden metin blok üretir."""
        try:
            lines: List[str] = [
                "=" * width,
                " Sistem Telemetri — Canlı Özet",
                "=" * width,
                f" Zaman: {report.get('generated_at', '-')}",
                "-" * width,
            ]
            collectors = report.get("collectors", {})
            readings = collectors.get("readings")
            if isinstance(readings, list) and readings:
                lines.append(f" Okumalar: {len(readings)} satir")
                for r in readings[:12]:
                    if isinstance(r, dict):
                        st = str(r.get("status", ""))
                        line = (
                            f"   {r.get('component')}/{r.get('sensor')} "
                            f"{r.get('metric')}={r.get('value')}{r.get('unit')} "
                            f"[{st}]"
                        )
                        lines.append(_wrap_status(line, st))
                    else:
                        lines.append(f"   {r}")
                if len(readings) > 12:
                    lines.append(f"   ... +{len(readings) - 12} daha")
            else:
                for key, val in collectors.items():
                    lines.append(f" [{key}]")
                    lines.append(f"   {val}")
            analysis = report.get("analysis")
            if analysis:
                lines.append("-" * width)
                lines.append(" Analiz:")
                if isinstance(analysis, dict):
                    lb = analysis.get("layer_b_trend")
                    if isinstance(lb, dict):
                        tr = lb.get("cpu_temperature_trend", "?")
                        slope = lb.get("slope_c_per_sec", "?")
                        n = lb.get("sample_count", "?")
                        lines.append(
                            f"   {_DIM}Katman B (CPU sicaklik trendi):{_RESET} "
                            f"{tr}  (ornek={n}, egim~{slope} C/s)",
                        )
                    lines.append(f"   {_DIM}(tam JSON aşağıda){_RESET}")
                lines.append(f"   {analysis}")
            health = report.get("health")
            if health is not None:
                lines.append("-" * width)
                hl = f" Sağlık: {health}"
                lines.append(_wrap_health_line(hl, health))
            alerts = report.get("alerts")
            if alerts:
                lines.append("-" * width)
                lines.append(f" Uyarılar ({len(alerts)}):")
                for a in alerts[:20]:
                    if isinstance(a, dict):
                        code = a.get("code", "")
                        prefix = f"[{a.get('severity', '?')}]"
                        if code:
                            prefix = f"{prefix} {code}"
                        lines.append(f"   • {prefix} {a.get('title', a)}")
                    else:
                        lines.append(f"   • {a}")
                if len(alerts) > 20:
                    lines.append(f"   ... ve {len(alerts) - 20} tane daha")
            lines.append("=" * width)
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("CLI render hatası: %s", exc)
            raise

    def print_report(self, report: Dict[str, Any]) -> None:
        """Özeti stdout'a yazdırır."""
        try:
            text = self.render(report)
            try:
                print(text, flush=True)
            except UnicodeEncodeError:
                print(text.encode("ascii", errors="replace").decode("ascii"), flush=True)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Yazdırma hatası: %s", exc)
            raise

    def print_live(self, report: Dict[str, Any], clear: bool = True) -> None:
        """Canlı panel: isteğe bağlı temizlik + rapor."""
        try:
            if clear:
                self.clear_screen()
            self.print_report(report)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Canlı panel hatası: %s", exc)
            raise
