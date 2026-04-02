"""Telemetri dict listelerini matplotlib icin X/Y listelerine donusturur."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple


def _iso_to_hhmmss(timestamp: str) -> str:
    """ISO zaman damgasindan okunur HH:MM:SS (matplotlib eksen etiketi)."""
    if not timestamp:
        return "--:--:--"
    s = str(timestamp).strip()
    m = re.search(r"(\d{2}:\d{2}:\d{2})", s)
    if m:
        return m.group(1)
    return s[:8]


def prepare_chart_data(
    telemetry_list: Sequence[Dict[str, Any]],
    *,
    component: str = "cpu",
    metric: str = "temperature",
) -> Tuple[List[str], List[float]]:
    """
    Veritabanindan gelen ozet satirlari -> matplotlib icin zaman listesi + deger listesi.

    - ``component`` / ``metric`` karsilastirmasi **kucuk harf** (``cpu``, ``temperature``).
    - Zaman ekseni: ``HH:MM:SS`` (okunabilir etiket).
    - Cikis sirasi: **eskiden yeniye** (``timestamp`` artan).

    Ornek::

        times, values = prepare_chart_data(rows, component="cpu", metric="temperature")
    """
    comp_l = str(component).lower().strip()
    met_l = str(metric).lower().strip()

    matched = [
        d
        for d in telemetry_list
        if str(d.get("component", "")).lower() == comp_l
        and str(d.get("metric", "")).lower() == met_l
    ]
    matched.sort(key=lambda d: str(d.get("timestamp", "")))

    times: List[str] = []
    values: List[float] = []
    for d in matched:
        try:
            v = float(d.get("value", 0.0))
        except (TypeError, ValueError):
            continue
        times.append(_iso_to_hhmmss(str(d.get("timestamp", ""))))
        values.append(v)

    return times, values
