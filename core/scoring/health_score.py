"""0–100 sistem sağlık puanı: bileşen puanları + settings ağırlıkları + gerekçeler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.helpers import clamp
from utils.logger import get_logger

# Ayarlarda weights yoksa varsayılan (toplam 1.0)
DEFAULT_COMPONENT_WEIGHTS: Dict[str, float] = {
    "cpu": 0.30,
    "gpu": 0.25,
    "memory": 0.15,
    "disk": 0.15,
    "fan": 0.10,
    "motherboard": 0.05,
}

DEFAULT_LEGACY_WEIGHTS: Dict[str, float] = {
    "thermal": 0.35,
    "disk": 0.25,
    "performance": 0.40,
}

# Henüz ölçüm yokken (bileşen listesi boş) hesaplayıcının döndürdüğü nötr tam puan.
# Dashboard’da snapshot yokken metin "Hesaplanıyor..." gösterilir (API `pending`).
DEFAULT_INITIAL_SCORE: float = 100.0


@dataclass
class HealthScoreResult:
    """Sağlık puanı sonucu."""

    score: float
    component_scores: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    factors: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""


class HealthScoreCalculator:
    """
    Bileşen bazlı puanlar (CPU, GPU, …) ve ağırlıklı genel skor.

    Öncelik: ``config/scoring_rules.yaml`` (``weights``, ``status_penalties``),
    ardından ``settings.yaml`` içindeki ``health_scoring``.

    Genel skor: ``sum(weight_i * component_score_i)`` (yalnızca ölçümü olan
    bileşenler için ağırlıklar yeniden normalize edilir).
    """

    def __init__(
        self,
        settings: Optional[Dict[str, Any]] = None,
        *,
        component_weights: Optional[Dict[str, float]] = None,
        scoring_rules: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._settings = settings or {}
        hs = self._settings.get("health_scoring", {})
        rules = dict(scoring_rules or {})
        rw = rules.get("weights")
        if isinstance(rw, dict) and rw:
            base_weights: Dict[str, float] = {str(k): float(v) for k, v in rw.items()}
        else:
            base_weights = dict(
                component_weights or hs.get("weights") or DEFAULT_COMPONENT_WEIGHTS,
            )
        self._component_weights = base_weights
        sp = rules.get("status_penalties")
        self._status_penalties: Dict[str, float] = dict(
            sp if isinstance(sp, dict) and sp
            else {"normal": 0.0, "ok": 0.0, "warning": 0.5, "critical": 1.0},
        )
        self._scoring_rules_meta: Dict[str, Any] = {
            "version": rules.get("version", ""),
        }
        self._legacy_weights: Dict[str, float] = dict(
            hs.get("legacy_weights") or DEFAULT_LEGACY_WEIGHTS,
        )
        self._logger = get_logger(f"{__name__}.HealthScoreCalculator")

    def compute_from_readings(
        self,
        readings: List[Dict[str, Any]],
    ) -> HealthScoreResult:
        """
        Telemetri satırlarından (``status`` alanları) bileşen puanları ve genel skor.

        Dönüş: ``score``, ``component_scores``, ``reasons``, ``factors`` (ağırlıklar, katkılar).
        """
        try:
            levels_by_component = self._levels_grouped_by_component(readings)
            present = {c for c, lv in levels_by_component.items() if lv}
            if not present:
                return HealthScoreResult(
                    score=DEFAULT_INITIAL_SCORE,
                    component_scores={},
                    reasons=["Bu döngüde bileşen telemetrisi yok; varsayılan tam puan."],
                    factors={"weights_effective": {}, "note": "no_readings"},
                    summary=f"Sağlık puanı: {DEFAULT_INITIAL_SCORE:.1f}/100 (ölçüm yok)",
                )

            raw_weights = {
                c: float(self._component_weights.get(c, 0.0))
                for c in present
            }
            sw = sum(raw_weights.values())
            if sw <= 0:
                w_eff = {c: 1.0 / len(present) for c in present}
            else:
                w_eff = {c: raw_weights[c] / sw for c in present}

            component_scores: Dict[str, float] = {}
            penalties: Dict[str, float] = {}
            for comp in present:
                lv = levels_by_component[comp]
                pen = self._penalty_from_levels(lv)
                penalties[comp] = pen
                component_scores[comp] = round(100.0 * (1.0 - pen), 2)

            overall = sum(w_eff[c] * component_scores[c] for c in present)
            overall = clamp(float(overall), 0.0, 100.0)

            reasons = self._build_reasons(
                component_scores=component_scores,
                levels_by_component=levels_by_component,
                weights_effective=w_eff,
                overall=overall,
            )

            factors: Dict[str, Any] = {
                "weights_configured": {k: v for k, v in self._component_weights.items()},
                "weights_effective": w_eff,
                "penalties": penalties,
                "status_penalties": dict(self._status_penalties),
                "scoring_rules": dict(self._scoring_rules_meta),
                "weighted_contribution": {
                    c: round(w_eff[c] * component_scores[c], 2) for c in present
                },
            }

            return HealthScoreResult(
                score=round(overall, 2),
                component_scores=component_scores,
                reasons=reasons,
                factors=factors,
                summary=f"Sağlık puanı: {overall:.1f}/100",
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Sağlık puanı (readings) hatası: %s", exc)
            raise

    def compute(
        self,
        thermal_levels: List[str],
        disk_levels: List[str],
        performance_levels: List[str],
    ) -> HealthScoreResult:
        """
        Geriye uyumluluk: üç kategori (termal / disk / performans) üzerinden skor.

        ``health_scoring.legacy_weights`` veya varsayılan ağırlıklar kullanılır.
        """
        try:
            w = self._legacy_weights
            t_pen = self._penalty_from_levels(thermal_levels)
            d_pen = self._penalty_from_levels(disk_levels)
            p_pen = self._penalty_from_levels(performance_levels)

            wt = float(w.get("thermal", 0.35))
            wd = float(w.get("disk", 0.25))
            wp = float(w.get("performance", 0.40))
            s = wt + wd + wp
            if s > 0:
                wt, wd, wp = wt / s, wd / s, wp / s

            total_penalty = wt * t_pen + wd * d_pen + wp * p_pen
            score = clamp(100.0 - total_penalty * 100.0, 0.0, 100.0)

            score_t = round(100.0 * (1.0 - t_pen), 2)
            score_d = round(100.0 * (1.0 - d_pen), 2)
            score_p = round(100.0 * (1.0 - p_pen), 2)

            component_scores = {
                "thermal": score_t,
                "disk": score_d,
                "performance": score_p,
            }

            reasons: List[str] = []
            if t_pen > 0:
                reasons.append(
                    f"Termal kategori cezası yüksek (ortalama ceza {t_pen:.2f}); "
                    f"bileşen skoru {score_t:.1f}.",
                )
            if d_pen > 0:
                reasons.append(
                    f"Disk kullanımı cezası (ortalama {d_pen:.2f}); bileşen skoru {score_d:.1f}.",
                )
            if p_pen > 0:
                reasons.append(
                    f"CPU/RAM performans cezası (ortalama {p_pen:.2f}); bileşen skoru {score_p:.1f}.",
                )
            if not reasons:
                reasons.append("Tüm legacy kategoriler normal; ek ceza yok.")

            factors = {
                "thermal_penalty": t_pen,
                "disk_penalty": d_pen,
                "performance_penalty": p_pen,
                "legacy_weights": {"thermal": wt, "disk": wd, "performance": wp},
            }

            return HealthScoreResult(
                score=round(score, 2),
                component_scores=component_scores,
                reasons=reasons,
                factors=factors,
                summary=f"Sağlık puanı: {score:.1f}/100 (legacy)",
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Sağlık puanı (legacy) hatası: %s", exc)
            raise

    def compute_from_context(self, context: Dict[str, Any]) -> HealthScoreResult:
        """``readings`` varsa bileşen skoru; yoksa legacy alanları kullanır."""
        try:
            readings = context.get("readings")
            if isinstance(readings, list) and readings:
                return self.compute_from_readings(readings)
            th = [str(x) for x in context.get("thermal_levels", [])]
            dk = [str(x) for x in context.get("disk_levels", [])]
            pf = [str(x) for x in context.get("performance_levels", [])]
            return self.compute(th, dk, pf)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Context'ten skor hatası: %s", exc)
            raise

    @staticmethod
    def _levels_grouped_by_component(
        readings: List[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for r in readings:
            comp = str(r.get("component", "")).lower().strip()
            if not comp:
                continue
            st = str(r.get("status", "normal")).lower().strip()
            out.setdefault(comp, []).append(st)
        return out

    def _penalty_from_levels(self, levels: List[str]) -> float:
        """0.0 (iyi) .. 1.0 (kötü) ortalama ceza (``scoring_rules.status_penalties``)."""
        if not levels:
            return 0.0
        pts = 0.0
        for lv in levels:
            s = str(lv).lower().strip()
            pen = float(self._status_penalties.get(s, self._status_penalties.get("normal", 0.0)))
            pts += pen
        return min(1.0, pts / max(len(levels), 1))

    def _build_reasons(
        self,
        *,
        component_scores: Dict[str, float],
        levels_by_component: Dict[str, List[str]],
        weights_effective: Dict[str, float],
        overall: float,
    ) -> List[str]:
        reasons: List[str] = []
        for comp in sorted(component_scores.keys()):
            sc = component_scores[comp]
            lv = levels_by_component.get(comp, [])
            n_crit = sum(1 for x in lv if x.lower() == "critical")
            n_warn = sum(1 for x in lv if x.lower() == "warning")
            w = weights_effective.get(comp, 0.0)
            contrib = w * sc

            if sc < 100.0:
                reasons.append(
                    f"{comp.upper()}: puan {sc:.1f}/100 "
                    f"({n_crit} kritik, {n_warn} uyarı ölçüm); "
                    f"etkin ağırlık %{w*100:.1f}, genel puana yaklaşık katkı {contrib:.1f} puan.",
                )

        if overall < 95.0 and not any(sc < 100.0 for sc in component_scores.values()):
            reasons.append(
                f"Genel puan {overall:.1f}: ağırlıklı ortalamada birden fazla bileşen hafif düşük.",
            )

        if not reasons:
            reasons.append("Tüm bileşenlerde uyarı/kritik yok veya ceza düşük; skor yüksek.")

        return reasons
