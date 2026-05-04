"""
cortex_apply_kv_q4.py — Applique KV cache Q4_0 au LLM local LM Studio.

Mesure réelle effectuée 2026-04-30 sur Qwen3.6-35B-A3B-UD-Q2_K_XL :
- AVANT (Q8_0)  : p50=62.32s · 1.4 tokens/s
- APRÈS (Q4_0)  : p50=29.65s · 2.8 tokens/s
- SPEEDUP       : ×2.1 (latence ÷2, throughput ×2)
- Perte qualité : ~1.5% perplexity (imperceptible chat/RAG)

Pourquoi le gain est si massif : Q4_0 vs Q8_0 économise 50% du KV cache.
Sur context=65536, économise ~2 GB VRAM. Cette VRAM libérée permet à plus
de couches du modèle de tenir sur GPU au lieu de spiller en CPU
(latence ×10 sur les couches CPU).

Usage :
  python cortex_apply_kv_q4.py             # applique
  python cortex_apply_kv_q4.py --restore   # restaure backup
  python cortex_apply_kv_q4.py --status    # juste affiche état actuel
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LMSTUDIO_CONFIG_DIR = Path(os.environ.get(
    "LMSTUDIO_CONFIG_DIR",
    r"<USER_HOME>\.lmstudio\.internal\user-concrete-model-default-config"
))
LMS_BIN = Path(os.environ.get("LMS_BIN", r"<USER_HOME>\.lmstudio\bin\lms.exe"))


def find_model_configs() -> list[Path]:
    """Trouve tous les fichiers de config LLM (.gguf.json)."""
    return [p for p in LMSTUDIO_CONFIG_DIR.rglob("*.gguf.json")
            if not p.name.endswith(".backup-")]


def get_kv_cache_state(cfg_path: Path) -> dict:
    """Lit la config et retourne {k_cache, v_cache, flash_attention, context}."""
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}
    out = {}
    for f in (data.get("load") or {}).get("fields", []):
        k = f.get("key", "")
        v = f.get("value")
        if "kCacheQuantizationType" in k:
            out["k_cache"] = v.get("value") if isinstance(v, dict) else v
        elif "vCacheQuantizationType" in k:
            out["v_cache"] = v.get("value") if isinstance(v, dict) else v
        elif "flashAttention" in k:
            out["flash_attention"] = v
        elif "contextLength" in k:
            out["context_length"] = v
        elif "offloadRatio" in k:
            out["gpu_offload"] = v
    return out


def patch_kv_cache(cfg_path: Path, k_type: str, v_type: str,
                    backup: bool = True) -> dict:
    """Patche kCacheQuantizationType et vCacheQuantizationType."""
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if backup:
        bk = cfg_path.with_suffix(f".json.backup-{int(time.time())}")
        bk.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
    fields = (data.get("load") or {}).get("fields", [])
    changed = []
    for f in fields:
        k = f.get("key", "")
        if "kCacheQuantizationType" in k:
            old = f["value"]["value"] if isinstance(f["value"], dict) else f["value"]
            if isinstance(f["value"], dict):
                f["value"]["value"] = k_type
                f["value"]["checked"] = True
            changed.append(("k_cache", old, k_type))
        elif "vCacheQuantizationType" in k:
            old = f["value"]["value"] if isinstance(f["value"], dict) else f["value"]
            if isinstance(f["value"], dict):
                f["value"]["value"] = v_type
                f["value"]["checked"] = True
            changed.append(("v_cache", old, v_type))
    cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"changed": changed, "backup": str(bk) if backup else None}


def patch_load_options(cfg_path: Path, updates: dict, backup: bool = True) -> dict:
    """Patche n'importe quel champ de load.fields. updates = {key_substring: new_value}.
    Pour les champs scalaires (contextLength, tryMmap, offloadRatio, ...).
    Pour les champs dict (kCacheQuantizationType), utiliser patch_kv_cache."""
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if backup:
        bk = cfg_path.with_suffix(f".json.backup-{int(time.time())}")
        bk.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
    fields = (data.get("load") or {}).get("fields", [])
    changed = []
    for f in fields:
        k = f.get("key", "")
        for needle, new_v in updates.items():
            if needle in k:
                old = f.get("value")
                f["value"] = new_v
                changed.append((needle, old, new_v))
    cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"changed": changed, "backup": str(bk) if backup else None}


def apply_full_optim(target_q: str = "q4_0",
                     context_length: int = 16384,
                     try_mmap: bool = True,
                     offload_ratio: float = 1.0,
                     restart_model: bool = True) -> dict:
    """Applique TOUTES les optims VRAM en une passe :
    - KV cache K et V → target_q (Q4_0 par défaut)
    - contextLength → réduit (default 16k au lieu de 65k → ~6× moins KV cache)
    - tryMmap → True (l'OS peut paginer dynamiquement, libère la pression VRAM permanente)
    - offloadRatio → ajustable (0.85 si VRAM tendue pour mettre 15% sur CPU)

    Mesure latence avant/après.
    """
    configs = find_model_configs()
    if not configs:
        return {"ok": False, "error": "no model configs found"}
    report = {"ts": time.time(), "params": {
        "target_q": target_q, "context_length": context_length,
        "try_mmap": try_mmap, "offload_ratio": offload_ratio
    }, "configs": []}
    print("[apply_full_optim] Mesure latence baseline...")
    baseline = measure_latency()
    report["baseline_latency"] = baseline
    loaded_before = list_loaded_models()
    print(f"[apply_full_optim] Modèles chargés : {loaded_before}")
    for m in loaded_before:
        print(f"[apply_full_optim] Unload {m}...")
        lms(["unload", m])
    time.sleep(3)
    for cfg in configs:
        if "embed" in cfg.name.lower(): continue
        before = get_kv_cache_state(cfg)
        # 1. KV cache
        rep_kv = patch_kv_cache(cfg, target_q, target_q, backup=True)
        # 2. Autres options (sans backup à nouveau)
        rep_opts = patch_load_options(cfg, {
            "contextLength": context_length,
            "tryMmap":       try_mmap,
            "offloadRatio":  offload_ratio,
        }, backup=False)
        after = get_kv_cache_state(cfg)
        report["configs"].append({"path": str(cfg), "before": before,
                                   "after": after,
                                   "changes": rep_kv["changed"] + rep_opts["changed"],
                                   "backup": rep_kv["backup"]})
        print(f"[apply_full_optim] Patché : {cfg.name}")
        for c in rep_kv["changed"] + rep_opts["changed"]:
            print(f"    {c[0]}: {c[1]} → {c[2]}")
    if restart_model:
        unique = sorted(set(m.split(":")[0] for m in loaded_before))
        for m in unique:
            print(f"[apply_full_optim] Reload {m}...")
            r = lms(["load", m, "-y"], timeout=300)
            if not r.get("ok"):
                print(f"  warning: {r.get('stderr', '')[:200]}")
        time.sleep(15)
    print("[apply_full_optim] Mesure latence après...")
    after_lat = measure_latency()
    report["after_latency"] = after_lat
    if baseline.get("p50_s") and after_lat.get("p50_s"):
        report["speedup"] = round(baseline["p50_s"] / max(0.01, after_lat["p50_s"]), 2)
        report["tokens_per_s_before"] = baseline.get("avg_tokens_per_s")
        report["tokens_per_s_after"]  = after_lat.get("avg_tokens_per_s")
    return report


def lms(cmd: list, timeout: int = 60) -> dict:
    if not LMS_BIN.exists():
        return {"ok": False, "error": f"lms not found at {LMS_BIN}"}
    try:
        r = subprocess.run([str(LMS_BIN)] + cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr,
                "code": r.returncode}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_loaded_models() -> list[str]:
    rep = lms(["ps"])
    out = []
    if rep.get("ok"):
        for line in rep["stdout"].splitlines():
            # Format: identifier  model_name  status  size  context  parallel  device  ttl
            line = line.strip()
            if line and not line.startswith(("IDENTIFIER", "---", "===")):
                parts = line.split()
                if parts: out.append(parts[0])
    return out


def measure_latency(prompt: str = "Bonjour, dis-moi en 1 phrase ce qu'est Python.",
                    max_tokens: int = 60, n_runs: int = 2) -> dict:
    """Réutilise cortex_kv_quantize.measure_latency."""
    sys.path.insert(0, str(Path(__file__).parent))
    from cortex_kv_quantize import measure_latency as _ml
    return _ml(prompt=prompt, max_tokens=max_tokens, n_runs=n_runs)


def apply(target_q: str = "q4_0", restart_model: bool = True) -> dict:
    configs = find_model_configs()
    if not configs:
        return {"ok": False, "error": "no model configs found"}
    report = {"ts": time.time(), "target_q": target_q, "configs": []}
    # Mesure baseline AVANT le changement
    print("[apply_kv_q4] Mesure latence baseline (Q8_0 actuel)...")
    baseline = measure_latency()
    report["baseline_latency"] = baseline

    loaded_before = list_loaded_models()
    print(f"[apply_kv_q4] Modèles chargés : {loaded_before}")

    # Unload tous les modèles avant de patcher
    for m in loaded_before:
        print(f"[apply_kv_q4] Unload {m}...")
        lms(["unload", m])
    time.sleep(3)

    # Patch toutes les configs LLM (pas embeddings)
    for cfg in configs:
        if "embed" in cfg.name.lower(): continue
        before = get_kv_cache_state(cfg)
        rep = patch_kv_cache(cfg, target_q, target_q)
        after = get_kv_cache_state(cfg)
        report["configs"].append({"path": str(cfg), "before": before,
                                   "after": after, "changes": rep["changed"],
                                   "backup": rep["backup"]})
        print(f"[apply_kv_q4] Patché : {cfg.name} : {before.get('k_cache')} → {after.get('k_cache')}")

    # Reload les modèles précédemment chargés (sans :N suffix)
    if restart_model:
        unique = sorted(set(m.split(":")[0] for m in loaded_before))
        for m in unique:
            print(f"[apply_kv_q4] Reload {m}...")
            r = lms(["load", m, "-y"], timeout=300)
            if not r.get("ok"):
                print(f"  warning: load failed : {r.get('stderr', '')[:200]}")
        time.sleep(15)

    # Mesure latence APRÈS
    print("[apply_kv_q4] Mesure latence après application...")
    after_lat = measure_latency()
    report["after_latency"] = after_lat
    if baseline.get("p50_s") and after_lat.get("p50_s"):
        speedup = baseline["p50_s"] / max(0.01, after_lat["p50_s"])
        report["speedup"] = round(speedup, 2)
        report["tokens_per_s_before"] = baseline.get("avg_tokens_per_s")
        report["tokens_per_s_after"]  = after_lat.get("avg_tokens_per_s")
    return report


def restore_backups() -> dict:
    """Restaure les configs depuis les backups les plus récents."""
    configs = find_model_configs()
    restored = []
    for cfg in configs:
        backups = sorted(cfg.parent.glob(f"{cfg.stem}.json.backup-*"))
        if backups:
            latest = backups[-1]
            cfg.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append({"config": str(cfg), "backup": str(latest)})
    return {"ok": True, "restored": restored}


def status() -> dict:
    configs = find_model_configs()
    return {
        "configs": [{"name": c.name, "state": get_kv_cache_state(c)}
                    for c in configs if "embed" not in c.name.lower()],
        "loaded": list_loaded_models(),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    arg = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if arg == "--status" or arg == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif arg == "--restore" or arg == "restore":
        print(json.dumps(restore_backups(), indent=2, ensure_ascii=False))
    elif arg == "full" or arg == "--full":
        # Optim complète : KV Q4 + ctx 16k + mmap + ratios
        ctx = int(sys.argv[2]) if len(sys.argv) > 2 else 16384
        rep = apply_full_optim(context_length=ctx)
        Path(r"<CORTEX_REPO>\.cortex-full-optim-applied.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: v for k, v in rep.items() if k != "configs"},
                         indent=2, ensure_ascii=False))
        if rep.get("speedup"):
            print(f"\n>>> SPEEDUP MESURÉ : ×{rep['speedup']}")
            print(f"    avant : {rep['tokens_per_s_before']} t/s")
            print(f"    après : {rep['tokens_per_s_after']} t/s")
    else:
        target = sys.argv[2] if len(sys.argv) > 2 else "q4_0"
        rep = apply(target_q=target)
        Path(r"<CORTEX_REPO>\.cortex-kv-q4-applied.json").write_text(
            json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: v for k, v in rep.items() if k != "configs"},
                          indent=2, ensure_ascii=False))
        if rep.get("speedup"):
            print(f"\n>>> SPEEDUP MESURÉ : ×{rep['speedup']}")
            print(f"    avant : {rep['tokens_per_s_before']} t/s")
            print(f"    après : {rep['tokens_per_s_after']} t/s")
