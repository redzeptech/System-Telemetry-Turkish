"""Kural tanımları ve değerlendirme."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.alerts.severity import Severity
from utils.logger import get_logger


@dataclass
class Rule:
    """Tek bir uyarı kuralı."""

    name: str
    condition: Callable[[Dict[str, Any]], bool]
    severity: Severity
    message_template: str


class RuleEngine:
    """Kural listesini veri üzerinde çalıştırır."""

    def __init__(self, rules: Optional[List[Rule]] = None) -> None:
        self._rules = rules or []
        self._logger = get_logger(f"{__name__}.RuleEngine")

    def add_rule(self, rule: Rule) -> None:
        """Kural ekler."""
        self._rules.append(rule)

    def evaluate(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Eşleşen kuralları döndürür."""
        fired: List[Dict[str, Any]] = []
        try:
            for rule in self._rules:
                try:
                    if rule.condition(context):
                        msg = rule.message_template.format(**context)
                        fired.append(
                            {
                                "rule": rule.name,
                                "severity": int(rule.severity),
                                "severity_name": rule.severity.name,
                                "message": msg,
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning("Kural '%s' hatası: %s", rule.name, exc)
            return fired
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("Kural değerlendirme hatası: %s", exc)
            raise
