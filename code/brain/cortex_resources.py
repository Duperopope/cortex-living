"""
cortex_resources.py — Cortex surveille les ressources de sa machine ('corps').

Utilisé par les loops autonomes (vision, reflection, consolidation) pour s'auto-limiter
quand le système est saturé. Permet aussi de tuer les processus zombies.

API :
- snapshot()         : retourne CPU%, RAM%, GPU si dispo
- can_spend_cpu()    : True si on peut lancer une tâche lourde
- list_cortex_procs() : processus Cortex actuellement vivants
- kill_zombies()     : tue les doublons (ex: 3 daemons xtts)
"""
import os
import time
from pathlib import Path

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


CPU_BUSY_THRESHOLD     = 85.0   # %
RAM_BUSY_THRESHOLD     = 88.0   # %
CORTEX_KEYWORDS = ("serve.py", "tts_monitor", "voice_input", "llm_router",
                   "xtts_daemon", "cortex_continuous")


def snapshot() -> dict:
    if not HAS_PSUTIL:
        return {"ok": False, "error": "psutil non installé"}
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    return {
        "ok": True,
        "cpu_percent": cpu,
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / 1e9, 2),
        "ram_total_gb": round(mem.total / 1e9, 2),
    }


def can_spend_cpu(threshold: float = CPU_BUSY_THRESHOLD,
                  ram_threshold: float = RAM_BUSY_THRESHOLD) -> tuple[bool, dict]:
    """Retourne (autorisé, snapshot). Si False, le caller doit reporter sa tâche."""
    snap = snapshot()
    if not snap.get("ok"): return True, snap   # pas de psutil → on tente
    busy = snap["cpu_percent"] > threshold or snap["ram_percent"] > ram_threshold
    return (not busy), snap


def list_cortex_procs() -> list[dict]:
    if not HAS_PSUTIL: return []
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "cpu_percent"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if any(kw in cmd for kw in CORTEX_KEYWORDS) and "powershell" not in cmd.lower():
                out.append({
                    "pid": p.info["pid"],
                    "name": next((kw for kw in CORTEX_KEYWORDS if kw in cmd), "?"),
                    "cmdline": cmd[:160],
                    "ram_mb": round(p.info["memory_info"].rss / 1e6, 1) if p.info["memory_info"] else 0,
                })
        except Exception: pass
    return out


def kill_zombies() -> dict:
    """Tue les doublons : si 2 instances du même service tournent, garde le plus vieux."""
    procs = list_cortex_procs()
    by_name = {}
    for p in procs:
        by_name.setdefault(p["name"], []).append(p)
    killed = []
    for name, instances in by_name.items():
        if len(instances) > 1 and HAS_PSUTIL:
            instances.sort(key=lambda x: x["pid"])  # PID le plus bas = le plus ancien
            for dup in instances[1:]:
                try:
                    psutil.Process(dup["pid"]).terminate()
                    killed.append({"name": name, "pid": dup["pid"]})
                except Exception: pass
    return {"ok": True, "killed": killed, "kept": len(by_name)}


def health_report() -> dict:
    snap = snapshot()
    procs = list_cortex_procs()
    return {
        "system": snap,
        "cortex_processes": procs,
        "process_count": len(procs),
        "total_cortex_ram_mb": sum(p.get("ram_mb", 0) for p in procs),
    }


if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        print(json.dumps(health_report(), ensure_ascii=False, indent=2))
    elif cmd == "kill_zombies":
        print(json.dumps(kill_zombies(), ensure_ascii=False, indent=2))
    elif cmd == "can":
        ok, snap = can_spend_cpu()
        print(json.dumps({"can_spend": ok, **snap}, ensure_ascii=False, indent=2))
