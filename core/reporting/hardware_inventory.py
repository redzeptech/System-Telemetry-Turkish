"""Donanım envanteri: platform, psutil, Windows WMI; GPU için GPUtil / NVML yedek."""

from __future__ import annotations

import platform
import re
import subprocess
from typing import Dict

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


def _dash() -> str:
    return "—"


def _format_ram_gb(total_bytes: int) -> str:
    if total_bytes <= 0:
        return _dash()
    gb = total_bytes / (1024.0**3)
    return f"{gb:.2f} GB"


def _cpu_windows_wmi() -> str:
    try:
        import wmi  # type: ignore[import-untyped]

        c = wmi.WMI()
        for cpu in c.Win32_Processor():
            name = getattr(cpu, "Name", None) or getattr(cpu, "Caption", None)
            if name and str(name).strip():
                return str(name).strip()
    except Exception:
        pass
    return ""


def _gpu_windows_wmi() -> str:
    try:
        import wmi  # type: ignore[import-untyped]

        c = wmi.WMI()
        names: list[str] = []
        for g in c.Win32_VideoController():
            n = getattr(g, "Name", None)
            if n and str(n).strip():
                names.append(str(n).strip())
        if names:
            seen: set[str] = set()
            uniq: list[str] = []
            for n in names:
                if n not in seen:
                    seen.add(n)
                    uniq.append(n)
            return " | ".join(uniq[:4])
    except Exception:
        pass
    return ""


def _cpu_linux_proc() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.lower().startswith("model name") or line.lower().startswith("cpu model"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _cpu_darwin_sysctl() -> str:
    try:
        r = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _gpu_gputil() -> str:
    try:
        import GPUtil

        gpus = GPUtil.getGPUs()
        if gpus:
            return " | ".join(g.name for g in gpus[:4])
    except Exception:
        pass
    return ""


def _gpu_nvml() -> str:
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            n = pynvml.nvmlDeviceGetCount()
            names: list[str] = []
            for i in range(min(int(n), 4)):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                raw = pynvml.nvmlDeviceGetName(h)
                if isinstance(raw, bytes):
                    names.append(raw.decode("utf-8", errors="replace"))
                else:
                    names.append(str(raw))
            if names:
                return " | ".join(names)
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        pass
    return ""


def _os_version_string() -> str:
    try:
        uname = platform.uname()
        parts = [platform.system(), platform.release()]
        if uname.version:
            v = uname.version.strip()
            if v and len(v) < 120:
                parts.append(v)
        return " ".join(p for p in parts if p)
    except Exception:
        return platform.platform()


def collect_hardware_inventory() -> Dict[str, str]:
    """
    CPU modeli, GPU adı, toplam RAM, işletim sistemi.

    Anahtarlar: ``cpu``, ``gpu``, ``ram``, ``os`` (PDF için kısa metin).
    """
    out: Dict[str, str] = {"cpu": _dash(), "gpu": _dash(), "ram": _dash(), "os": _dash()}

    out["os"] = _os_version_string() or _dash()

    if psutil is not None:
        try:
            out["ram"] = _format_ram_gb(int(psutil.virtual_memory().total))
        except Exception:
            out["ram"] = _dash()

    sys = platform.system()
    cpu = ""
    if sys == "Windows":
        cpu = _cpu_windows_wmi()
    elif sys == "Linux":
        cpu = _cpu_linux_proc()
    elif sys == "Darwin":
        cpu = _cpu_darwin_sysctl()
    if not cpu:
        try:
            cpu = platform.processor() or ""
        except Exception:
            cpu = ""
    if not cpu:
        try:
            cpu = platform.machine() or ""
        except Exception:
            cpu = ""
    out["cpu"] = cpu.strip() if cpu else _dash()

    gpu = ""
    if sys == "Windows":
        gpu = _gpu_windows_wmi()
    if not gpu:
        gpu = _gpu_gputil()
    if not gpu:
        gpu = _gpu_nvml()
    out["gpu"] = gpu.strip() if gpu else _dash()

    for k in out:
        s = str(out[k])
        s = re.sub(r"\s+", " ", s).strip()
        out[k] = s if s else _dash()

    return out


# Rapor tetikleyicileri / eski isimler
get_system_hardware = collect_hardware_inventory
