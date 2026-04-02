"""thresholds.yaml içinde bileşen (CPU, GPU, ...) bölümlerine güvenli erişim."""

from __future__ import annotations

from typing import Any, Dict

# Telemetri component (küçük harf) -> YAML kök anahtarı
COMPONENT_TO_YAML_KEY: Dict[str, str] = {
    "cpu": "CPU",
    "gpu": "GPU",
    "motherboard": "Motherboard",
    "memory": "Memory",
    "disk": "Disk",
    "fan": "Fan",
}


def get_component_section(thresholds: Dict[str, Any], component: str) -> Dict[str, Any]:
    """
    ``CPU:`` / ``GPU:`` gibi YAML bloklarını döndürür (anahtar büyük/küçük harf duyarsız).
    """
    if not thresholds:
        return {}
    yaml_key = COMPONENT_TO_YAML_KEY.get(component.lower())
    if not yaml_key:
        return {}
    if yaml_key in thresholds and isinstance(thresholds[yaml_key], dict):
        return thresholds[yaml_key]
    for k, v in thresholds.items():
        if isinstance(k, str) and k.upper() == yaml_key.upper() and isinstance(v, dict):
            return v
    return {}


def get_metric_config(
    thresholds: Dict[str, Any],
    component: str,
    metric: str,
) -> Dict[str, Any]:
    """Örn. ``CPU`` → ``temperature`` alt sözlüğü."""
    section = get_component_section(thresholds, component)
    raw = section.get(metric)
    return raw if isinstance(raw, dict) else {}
