#!/usr/bin/env python3
"""
Stres testi: arka planda CPU yükü, önde Orchestrator döngüleri.

Beklenen gözlem (donanıma göre değişir):
- ``layer_b_trend`` içinde ``cpu_temperature_trend``: ``rising`` (Linux psutil sıcaklığı
  veya LHM ile CPU sıcaklığı varsa; yalnızca yük varsa ``unknown`` olabilir).
- ``health`` ``score``: 100 altı (uyarı/kritik satırlar).
- Konsol: yeşil/sarı/kırmızı ANSI (``[warning]`` / ``[critical]`` satırları, düşük skor).

Güvenlik: kısa süreli CPU yükü tipik masaüstünde güvenlidir; termal korumaya güvenin.
Durdurma: Ctrl+C
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import Orchestrator  # noqa: E402
from utils.helpers import load_yaml  # noqa: E402
from utils.logger import setup_logger  # noqa: E402

# Döngü sayısı ve bekleme (Katman B için en az ~3 örnek + eğim)
DEFAULT_CYCLES = 20
DEFAULT_SLEEP_SEC = 2.0


def _burn(stop: mp.Event) -> None:
    while not stop.is_set():
        _ = sum(i * i for i in range(8000))


def main() -> None:
    parser = argparse.ArgumentParser(description="CPU stresi + telemetri gözlem")
    parser.add_argument(
        "--cycles",
        type=int,
        default=DEFAULT_CYCLES,
        help=f"döngü sayısı (varsayılan {DEFAULT_CYCLES})",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SEC,
        help="döngüler arası saniye",
    )
    args = parser.parse_args()

    setup_logger("stress_watch")
    cfg = ROOT / "config"
    settings = load_yaml(cfg / "settings.yaml")
    n_proc = max(1, (mp.cpu_count() or 2) - 1)

    stop = mp.Event()
    workers = [mp.Process(target=_burn, args=(stop,)) for _ in range(n_proc)]
    for w in workers:
        w.start()

    print(
        f"\n{'=' * 60}\n"
        f" STRES: {n_proc} süreç CPU yükü | {args.cycles} döngü × ~{args.sleep}s\n"
        " Katman B (rising): CPU sıcaklık satırı gerekir (Linux psutil veya LHM).\n"
        " Yük uyarıları: CPU load satırlarında sarı/kırmızı + sağlık skoru düşer.\n"
        f"{'=' * 60}\n",
        flush=True,
    )
    try:
        orch = Orchestrator(cfg, settings)
        for _ in range(args.cycles):
            orch.run_cycle(dashboard_live=True, clear_screen=True)
            time.sleep(args.sleep)
    except KeyboardInterrupt:
        print("\nCtrl+C — stres durduruluyor.\n", flush=True)
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=4)
            if w.is_alive():
                w.terminate()


if __name__ == "__main__":
    main()
