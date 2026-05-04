"""
cortex_continuous.py — Cognition continue de Cortex.

Thread daemon qui tourne en arrière-plan et :
1. Idle ~10 min → réfléchit à une question/connexion intéressante
2. Génère une "réflexion" via le router v2
3. La sauvegarde dans 07 - Ingested/reflections/<date>/<time>-<slug>.md
4. Si la réflexion révèle un goal d'amélioration concret, la passe à cortex_self_dev

C'est ici que Cortex "vit" entre les requêtes utilisateur.
"""
import datetime as dt
import json
import random
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

VAULT_PATH    = Path(r"<USER_HOME>\Documents\Obsidian Vault")
REFLECT_DIR   = VAULT_PATH / "07 - Ingested" / "reflections"
ROUTER_URL    = "http://127.0.0.1:18900/route_v2"
LOG_FILE      = Path(r"<CORTEX_REPO>\.cortex-continuous.log")

REFLECT_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str):
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


THINKING_SEEDS = [
    "Quelle question Sam pourrait-il me poser dans les prochains jours ?",
    "Quel est le pattern dominant des dernières conversations ?",
    "Quelle est la prochaine étape logique du projet Paperclip ?",
    "Y a-t-il une contradiction entre deux mémoires récentes ?",
    "Qu'est-ce que je sais mal et que je devrais clarifier ?",
    "Quels sont les 3 concepts les plus connectés dans ma mémoire récente ?",
    "Si je devais résumer la session récente en 3 phrases, ce serait quoi ?",
    "Quelle amélioration technique aurait le plus d'impact sur le système ?",
]


def _ask_router(prompt: str, timeout: int = 60) -> str:
    """Appelle minimax direct via opencode (rapide, pas de v2 panel)."""
    try:
        import subprocess as _sp
        OPENCODE = r"<USER_HOME>\AppData\Roaming\npm\opencode.cmd"
        r = _sp.run([OPENCODE, "run", "--model", "opencode/minimax-m2.5-free", "-"],
                    input=prompt, capture_output=True, text=True,
                    timeout=timeout, encoding="utf-8", errors="replace")
        lines = [l for l in r.stdout.splitlines()
                 if l.strip() and not l.startswith(">") and "\x1b" not in l and "build" not in l.lower()]
        return "\n".join(lines).strip()
    except Exception as e:
        _log(f"opencode err: {e}")
        return ""


def _gather_recent_context(max_chars: int = 3000) -> str:
    """Récupère les 5 derniers échanges pour contextualiser la réflexion."""
    sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
    try:
        import cortex_memory as cm
        ing = cm.INGESTED_DIR
        recent = []
        for day in sorted(ing.glob("*"), reverse=True)[:3]:
            if day.is_dir():
                for note in sorted(day.glob("*.md"), reverse=True)[:5]:
                    try:
                        recent.append(note.read_text(encoding="utf-8", errors="replace")[:500])
                    except Exception: pass
                    if len(recent) >= 5: break
            if len(recent) >= 5: break
        joined = "\n\n---\n\n".join(recent)
        return joined[:max_chars]
    except Exception:
        return ""


def reflect_once() -> dict:
    """Une itération de réflexion. Retourne {seed, reflection, saved_path}."""
    seed = random.choice(THINKING_SEEDS)
    context = _gather_recent_context()
    prompt = (
        f"Tu es Cortex, en train de réfléchir en arrière-plan entre deux interactions.\n\n"
        f"Contexte récent (5 derniers échanges) :\n{context}\n\n"
        f"Question de réflexion : {seed}\n\n"
        f"Réponds en français, 2-4 phrases concrètes. Si tu identifies un goal "
        f"d'amélioration testable, termine par : `GOAL: <description courte>`."
    )
    _log(f"reflect: {seed[:60]!r}")
    response = _ask_router(prompt)
    if not response or len(response) < 30:
        return {"seed": seed, "skipped": "empty response"}

    # Sauvegarder la réflexion
    now = dt.datetime.now()
    day_dir = REFLECT_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^\w-]', '-', seed.lower())[:30].strip('-')
    fname = f"{now.strftime('%H-%M-%S')}-{slug}.md"
    path = day_dir / fname
    body = (
        f"---\n"
        f"captured_at: {now.isoformat(timespec='seconds')}\n"
        f"type: reflection\n"
        f"seed: {seed}\n"
        f"---\n\n"
        f"## Question\n\n{seed}\n\n"
        f"## Réflexion\n\n{response.strip()}\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
    except Exception as e:
        _log(f"save err: {e}")
        return {"seed": seed, "error": str(e)}

    # Si goal détecté, l'extraire pour future utilisation
    goal_match = re.search(r'GOAL:\s*(.+?)(?:\n|$)', response, re.I)
    goal = goal_match.group(1).strip() if goal_match else None

    return {"seed": seed, "saved_path": str(path), "goal": goal,
            "reflection_excerpt": response[:200]}


def vision_loop_once() -> dict:
    """Cortex regarde, auto-tune si nécessaire, génère une observation, mémorise."""
    try:
        import cortex_vision as cv
        if cv.is_vision_muted():
            return {"skipped": "muted"}
        cap = cv.capture_webcam()
        if not cap.get("ok"): return {"err": cap.get("error")}
        # Auto-tune si exposition foireuse
        import cv2
        img = cv2.imread(cap["path"])
        tune = cv.auto_tune_from_frame(img)
        # Décrire (vision LLM ou cv2 fallback)
        result = cv.see(prompt="Décris en 1-2 phrases ce que tu vois maintenant.", source="webcam")
        if result.get("ok"):
            # Mémoriser comme observation
            now = dt.datetime.now()
            day_dir = REFLECT_DIR.parent / "observations" / now.strftime("%Y-%m-%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            path = day_dir / f"{now.strftime('%H-%M-%S')}-vision.md"
            body = (
                f"---\ncaptured_at: {now.isoformat(timespec='seconds')}\n"
                f"type: vision_observation\nmethod: {result.get('method','?')}\n"
                f"camera_idx: {cap.get('camera_idx','?')}\n---\n\n"
                f"## Observation\n\n{result.get('description','')}\n\n"
                f"_Auto-tune: {tune}_\n"
            )
            try: path.write_text(body, encoding="utf-8")
            except: pass
            return {"ok": True, "description": result.get("description","")[:200],
                    "method": result.get("method"), "tune": tune}
        return {"ok": False, "err": result.get("error")}
    except Exception as e:
        return {"err": str(e)}


def _vision_loop_runner(interval: int):
    """Thread daemon de vision : skip si machine saturée."""
    time.sleep(180)
    try:
        import cortex_resources as cr
    except ImportError: cr = None
    while _running:
        try:
            # Skip si machine saturée
            if cr:
                ok, snap = cr.can_spend_cpu()
                if not ok:
                    _log(f"vision skipped (cpu={snap.get('cpu_percent','?')}% ram={snap.get('ram_percent','?')}%)")
                    time.sleep(interval); continue
            r = vision_loop_once()
            if r.get("ok"):
                _log(f"vision: {r.get('description','')[:80]}")
            elif r.get("skipped"):
                pass
            else:
                _log(f"vision err: {r.get('err','?')}")
        except Exception as e:
            _log(f"vision loop err: {e}")
        time.sleep(interval)


_running = False
_thread = None
_vision_thread = None
INTERVAL_SEC = 600  # 10 min entre réflexions
VISION_INTERVAL_SEC = 300  # 5 min entre observations visuelles


def _loop(interval: int):
    global _running
    time.sleep(120)
    try:
        import cortex_resources as cr
    except ImportError: cr = None
    while _running:
        try:
            if cr:
                ok, snap = cr.can_spend_cpu()
                if not ok:
                    _log(f"reflect skipped (cpu={snap.get('cpu_percent','?')}% ram={snap.get('ram_percent','?')}%)")
                    time.sleep(interval); continue
            reflect_once()
        except Exception as e:
            _log(f"loop err: {e}")
        time.sleep(interval)


def start(interval: int = INTERVAL_SEC, vision_interval: int = VISION_INTERVAL_SEC):
    global _running, _thread, _vision_thread
    if _running: return
    _running = True
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True)
    _thread.start()
    _vision_thread = threading.Thread(target=_vision_loop_runner, args=(vision_interval,), daemon=True)
    _vision_thread.start()
    _log(f"continuous cognition + vision loop started (reflect={interval}s, vision={vision_interval}s)")


def stop():
    global _running
    _running = False


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        print(json.dumps(reflect_once(), ensure_ascii=False, indent=2)[:2000])
    elif len(sys.argv) > 1 and sys.argv[1] == "loop":
        start(int(sys.argv[2]) if len(sys.argv) > 2 else INTERVAL_SEC)
        try:
            while True: time.sleep(60)
        except KeyboardInterrupt: stop()
    else:
        print("Usage: cortex_continuous.py once | loop [interval_sec]")
