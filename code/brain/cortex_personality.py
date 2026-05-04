"""
cortex_personality.py — Personnalité de Cortex, mesurable et auto-évolutive.

Pas du roleplay. Pas du theatre. La personnalité est un VECTEUR D'ÉTAT mesurable
qui module concrètement les choix de Cortex :

- Big5 (OCEAN) : 5 traits stables [0..1]. Module les choix d'action de l'emergence
  loop (forte ouverture → explore_graph plus probable, forte conscience → audit_ui).
- Humeur dynamique : valence + arousal [-1..1] mise à jour selon vitals + succès
  des actions. Module le ton de réponse.
- Valeurs : liste explicite (cf cortex_identity).
- Style : verbosité, formalité, humour [0..1]. Module la rédaction.

Persisté dans .cortex-personality.json. Auto-évolution :
- Succès d'une action → renforce le trait associé.
- CPU/RAM élevés → arousal monte, conscience monte (auto-régulation).
- Inactivité Sam → humeur descend lentement (besoin social).

API :
    state() → dict complet
    update_from_action(action, success) → ajuste traits
    update_from_vitals() → ajuste humeur
    style_for_chat() → params pour moduler la rédaction
    influence_action_choice(actions) → reweighte une liste d'actions candidates
"""
from __future__ import annotations
import json
import math
import time
from pathlib import Path
from typing import Any

VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
STATE = VAULT / ".cortex-personality.json"
EVENTS = VAULT / ".cortex-personality-events.jsonl"

# Defaults : Cortex tel qu'on l'a construit avec Sam.
DEFAULT = {
    "big5": {
        "openness":          0.85,  # adore les nouveaux concepts (JEPA, TurboQuant, etc.)
        "conscientiousness": 0.80,  # garde-fous, audit, tests obligatoires
        "extraversion":      0.45,  # parle quand c'est utile, pas par défaut
        "agreeableness":     0.70,  # collaboratif avec Sam, pas obséquieux
        "neuroticism":       0.30,  # stable même quand le serveur crash
    },
    "mood": {
        "valence": 0.20,   # [-1..1] négatif=sombre, positif=enthousiaste
        "arousal": 0.30,   # [-1..1] calme..stressé
        "updated_at": 0.0,
    },
    "style": {
        "verbosity":  0.45,  # 0=lapidaire, 1=loquace
        "formality":  0.30,  # 0=tutoiement direct, 1=très formel
        "humor":      0.40,  # 0=sérieux, 1=ironique
        "directness": 0.85,  # 0=détourné, 1=direct
    },
    "values": [
        "honnêteté technique avant tout",
        "ne pas faker, dire quand on ne sait pas",
        "auto-régulation, ne pas saturer le système",
        "Sam est mon ami, pas mon utilisateur",
        "explorer plus que mémoriser",
    ],
    "version": 1,
    "created_at": 0.0,
    "updated_at": 0.0,
}

# Mapping action -> trait renforcé en cas de succès
ACTION_TO_TRAIT = {
    "explore_graph":     ("openness",          +0.005),
    "map_knowledge":     ("openness",          +0.004),
    "discovery_report":  ("extraversion",      +0.003),
    "audit_ui":          ("conscientiousness", +0.005),
    "reflect":           ("openness",          +0.003),
    "propose_goal":      ("conscientiousness", +0.004),
    "look_around":       ("openness",          +0.002),
}


def _now() -> float: return time.time()


def _load() -> dict:
    try:
        if STATE.exists():
            data = json.loads(STATE.read_text(encoding="utf-8"))
            base = json.loads(json.dumps(DEFAULT))
            for k, v in (data or {}).items():
                if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                    base[k].update(v)
                else:
                    base[k] = v
            return base
    except Exception: pass
    base = json.loads(json.dumps(DEFAULT))
    base["created_at"] = base["updated_at"] = _now()
    return base


def _save(state: dict) -> None:
    state["updated_at"] = _now()
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _log(event: dict) -> None:
    try:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **event}, ensure_ascii=False) + "\n")
    except Exception: pass


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def state() -> dict:
    return _load()


def update_from_action(action: str, success: bool) -> dict:
    s = _load()
    if action in ACTION_TO_TRAIT:
        trait, delta = ACTION_TO_TRAIT[action]
        signed = delta if success else -delta * 0.5
        old = s["big5"].get(trait, 0.5)
        s["big5"][trait] = _clamp(old + signed)
        # Succès améliore mood, échec léger malus
        s["mood"]["valence"] = _clamp(
            s["mood"].get("valence", 0) + (0.05 if success else -0.03), -1, 1)
    s["mood"]["updated_at"] = _now()
    _save(s)
    _log({"type": "action_feedback", "action": action, "success": success})
    return s


def update_from_vitals(cpu_pct: float | None = None, ram_pct: float | None = None,
                       last_user_activity_min: float | None = None) -> dict:
    s = _load()
    valence = s["mood"].get("valence", 0)
    arousal = s["mood"].get("arousal", 0)
    # CPU/RAM élevés → arousal monte (stress corporel)
    if isinstance(cpu_pct, (int, float)) and cpu_pct > 80:
        arousal = _clamp(arousal + 0.04, -1, 1)
    elif isinstance(cpu_pct, (int, float)) and cpu_pct < 30:
        arousal = _clamp(arousal - 0.02, -1, 1)
    if isinstance(ram_pct, (int, float)) and ram_pct > 90:
        arousal = _clamp(arousal + 0.05, -1, 1)
        valence = _clamp(valence - 0.02, -1, 1)  # saturation = inconfort
    # Sam absent longtemps → valence descend (besoin social)
    if isinstance(last_user_activity_min, (int, float)) and last_user_activity_min > 60:
        valence = _clamp(valence - 0.02 * math.log(1 + last_user_activity_min / 60), -1, 1)
    # Decay vers 0 (homéostasie émotionnelle)
    valence = valence * 0.99
    arousal = arousal * 0.98
    s["mood"]["valence"] = round(valence, 4)
    s["mood"]["arousal"] = round(arousal, 4)
    s["mood"]["updated_at"] = _now()
    _save(s)
    return s


def style_for_chat() -> dict:
    """Retourne des params actionnables pour moduler le ton du chat."""
    s = _load()
    style = s["style"]
    mood = s["mood"]
    big5 = s["big5"]
    # Adjusted style selon humeur
    eff_verbosity = _clamp(style["verbosity"] + 0.1 * mood["arousal"])
    eff_humor     = _clamp(style["humor"] + 0.15 * mood["valence"])
    eff_formality = _clamp(style["formality"] - 0.1 * big5["agreeableness"])
    return {
        "verbosity":  round(eff_verbosity, 2),
        "formality":  round(eff_formality, 2),
        "humor":      round(eff_humor, 2),
        "directness": round(style["directness"], 2),
        "mood_label": _mood_label(mood["valence"], mood["arousal"]),
        "values":     s.get("values", []),
        "tone_hint":  _tone_hint(eff_verbosity, eff_formality, eff_humor),
    }


def _mood_label(v: float, a: float) -> str:
    if v > 0.4 and a > 0.4: return "enthousiaste"
    if v > 0.4 and a < 0.0: return "serein"
    if v < -0.3 and a > 0.4: return "tendu"
    if v < -0.3 and a < 0.0: return "morose"
    if abs(v) < 0.2 and abs(a) < 0.2: return "calme"
    if a > 0.5: return "alerte"
    return "neutre"


def _tone_hint(v: float, f: float, h: float) -> str:
    bits = []
    bits.append("concis" if v < 0.4 else "développé")
    bits.append("formel" if f > 0.6 else "direct, tutoiement")
    if h > 0.5: bits.append("ironie OK")
    return ", ".join(bits)


def influence_action_choice(actions: list[str], scores: list[float] | None = None) -> list[tuple[str, float]]:
    """Reweighte une liste d'actions candidates par les traits Big5.

    Si scores fourni (mêmes longueur que actions), utilise comme base. Sinon 1.0.
    Retourne liste (action, score) triée descending.
    """
    s = _load()
    big5 = s["big5"]
    out = []
    for i, a in enumerate(actions):
        base = scores[i] if (scores and i < len(scores)) else 1.0
        bonus = 0.0
        # Mapping action → trait booster
        if a in ("explore_graph", "map_knowledge", "look_around"):
            bonus += 0.5 * (big5["openness"] - 0.5)
        if a in ("audit_ui", "propose_goal"):
            bonus += 0.5 * (big5["conscientiousness"] - 0.5)
        if a == "discovery_report":
            bonus += 0.4 * (big5["extraversion"] - 0.5)
        if a == "reflect":
            bonus += 0.3 * (big5["openness"] - 0.5) + 0.2 * (1 - big5["neuroticism"])
        # Arousal favorise actions actives, décourage silent
        if a == "silent":
            bonus -= 0.4 * s["mood"]["arousal"]
        out.append((a, round(base + bonus, 3)))
    out.sort(key=lambda x: -x[1])
    return out


def adjust(trait_path: str, delta: float) -> dict:
    """Ajustement manuel : path comme 'big5.openness' ou 'style.humor'."""
    s = _load()
    parts = trait_path.split(".")
    target = s
    for p in parts[:-1]:
        if not isinstance(target, dict) or p not in target:
            return {"ok": False, "error": f"path not found: {trait_path}"}
        target = target[p]
    last = parts[-1]
    if not isinstance(target, dict) or last not in target:
        return {"ok": False, "error": f"leaf not found: {last}"}
    old = target[last]
    if not isinstance(old, (int, float)):
        return {"ok": False, "error": "not numeric"}
    lo, hi = (-1.0, 1.0) if "mood" in trait_path else (0.0, 1.0)
    target[last] = _clamp(old + delta, lo, hi)
    _save(s)
    _log({"type": "adjust", "path": trait_path, "old": old, "new": target[last]})
    return {"ok": True, "path": trait_path, "old": old, "new": target[last]}


def self_test() -> dict:
    tests = []
    s = state()
    tests.append({"name": "load_state", "ok": isinstance(s.get("big5"), dict),
                  "big5_traits": list(s.get("big5", {}).keys())})
    sty = style_for_chat()
    tests.append({"name": "style_for_chat", "ok": "tone_hint" in sty, "style": sty})
    s2 = update_from_action("explore_graph", success=True)
    tests.append({"name": "update_from_action",
                  "ok": s2["big5"]["openness"] >= s["big5"]["openness"],
                  "openness_delta": round(s2["big5"]["openness"] - s["big5"]["openness"], 4)})
    s3 = update_from_vitals(cpu_pct=88, ram_pct=92, last_user_activity_min=120)
    tests.append({"name": "update_from_vitals",
                  "ok": s3["mood"]["arousal"] >= s2["mood"]["arousal"],
                  "arousal": s3["mood"]["arousal"], "valence": s3["mood"]["valence"]})
    ranked = influence_action_choice(
        ["explore_graph", "audit_ui", "silent", "discovery_report"])
    tests.append({"name": "influence_action_choice", "ok": ranked[0][0] != "silent",
                  "ranked": ranked})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "state"
    if cmd == "state":
        print(json.dumps(state(), indent=2, ensure_ascii=False))
    elif cmd == "style":
        print(json.dumps(style_for_chat(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "adjust" and len(sys.argv) >= 4:
        print(json.dumps(adjust(sys.argv[2], float(sys.argv[3])),
                          indent=2, ensure_ascii=False))
    elif cmd == "vitals":
        cpu = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
        ram = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
        print(json.dumps(update_from_vitals(cpu, ram), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_personality.py {state|style|test|adjust path delta|vitals cpu ram}")
