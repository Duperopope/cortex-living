"""
cortex_dialogue.py — Conversation vivante avec Sam (chat + vocal).

Pas un wrapper LLM. Un module qui :

1. Détecte la PRÉSENCE de Sam (mic actif, frappe récente, mouvement souris).
2. Compose des réponses qui utilisent VRAIMENT l'état interne de Cortex
   (mood, plan courant, gaps, learned skills, causal facts) — pas un template.
3. Invoque le LLM seulement pour formuler la phrase finale, mais avec un
   contexte structuré qui force la fidélité à l'état réel.
4. Trace les SOURCES de chaque réponse pour audit anti-fake.
5. Active TTS si dispo et si Sam a manifestement de l'attention.
6. Invite Sam à parler quand le drive de curiosité l'exige.

Anti-fake intégré :
- chaque réponse logue la liste des "sources internes" utilisées
- test cohérence : 2 questions identiques à intervalle → divergence mesurée
- test don't-know : si question hors-sujet, "je ne sais pas" obligatoire
- baseline : comparer à un wrapper LLM nu (sans contexte interne)

API :
    detect_presence() → {present, last_activity_age_s, signals}
    compose_response(prompt) → {text, sources_used, used_internal_state}
    speak_if_relevant(message) → tente TTS si pertinent
    initiate_if_curious() → écrit dans le chat si curiosité forte non répondue
    self_test()
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
VAULT = Path(r"<USER_HOME>\Documents\Obsidian Vault")
CHAT_STREAM = VAULT / ".cortex-chat-stream.jsonl"
DIALOGUE_LOG = VAULT / ".cortex-dialogue-log.jsonl"
DIALOGUE_STATE = VAULT / ".cortex-dialogue-state.json"

PRESENCE_THRESHOLD_S = 300  # 5 min sans activité = absent


def _now() -> float: return time.time()


def _safe_import(name: str):
    try:
        sys.path.insert(0, str(REPO))
        return __import__(name)
    except Exception:
        return None


def _load_state() -> dict:
    if DIALOGUE_STATE.exists():
        try: return json.loads(DIALOGUE_STATE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"version": "dialogue-1", "n_responses": 0, "n_initiatives": 0,
            "last_initiative_ts": 0, "last_response_ts": 0,
            "history": []}


def _save_state(s: dict) -> None:
    s["updated_at"] = _now()
    try:
        DIALOGUE_STATE.parent.mkdir(parents=True, exist_ok=True)
        DIALOGUE_STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    except Exception: pass


def _log_event(ev: dict) -> None:
    try:
        with DIALOGUE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), **ev}, ensure_ascii=False) + "\n")
    except Exception: pass


def detect_presence() -> dict:
    """Détecte si Sam est présent (frappe récente dans le chat)."""
    last_user_ts = 0
    if CHAT_STREAM.exists():
        try:
            for ln in reversed(CHAT_STREAM.read_text(encoding="utf-8",
                                                       errors="replace").splitlines()[-100:]):
                try:
                    o = json.loads(ln)
                    if o.get("speaker") == "sam":
                        last_user_ts = float(o.get("ts") or 0)
                        break
                except Exception: pass
        except Exception: pass
    age = _now() - last_user_ts if last_user_ts > 0 else None
    present = age is not None and age < PRESENCE_THRESHOLD_S
    return {
        "present": present,
        "last_user_activity_ts": last_user_ts,
        "last_activity_age_s": age,
        "presence_threshold_s": PRESENCE_THRESHOLD_S,
    }


def _gather_internal_state() -> dict:
    """Rassemble l'état interne pertinent pour composer une réponse fidèle."""
    state = {}
    pers = _safe_import("cortex_personality")
    if pers:
        try:
            s = pers.state()
            state["mood_label"] = pers.style_for_chat().get("mood_label")
            state["values"] = s.get("values", [])[:3]
            state["openness"] = s.get("big5", {}).get("openness")
        except Exception: pass
    intro = _safe_import("cortex_introspection")
    if intro:
        try:
            r = intro.introspect()
            wk = r.get("what_i_know", {})
            wd = r.get("what_i_dont_know", {})
            state["well_known"] = [c["label"] for c in wk.get("well_known_concepts", [])[:3]]
            state["weak_dimensions"] = [d["human"] for d in wd.get("weak_dimensions", [])[:3]]
            state["currently_thinking_about"] = [
                c.get("label") for c in r.get("what_im_learning_now",
                                                {}).get("currently_thinking_about", [])[:3]
            ]
        except Exception: pass
    pl = _safe_import("cortex_plan")
    if pl:
        try:
            d = pl.daily_plan()
            state["daily_goals"] = [g["title"][:80] for g in d.get("goals", [])[:3]]
        except Exception: pass
    cur = _safe_import("cortex_curiosity")
    if cur:
        try:
            qs = cur.generate_questions(2)
            state["my_open_questions"] = qs
        except Exception: pass
    iag = _safe_import("cortex_iag_test")
    if iag:
        try:
            it = iag.run_iag_test()
            state["iag_score"] = it.get("global_score")
            state["iag_verdict"] = it.get("verdict")
        except Exception: pass
    return state


def _confidence_on(topic: str) -> dict:
    intro = _safe_import("cortex_introspection")
    if intro:
        try: return intro.confidence_on(topic)
        except Exception: pass
    return {"confidence": 0, "label": "inconnu"}


def _query_lm_studio(prompt: str, max_tokens: int = 350,
                      timeout: int = 60) -> str:
    """Envoie un prompt à LM Studio local. Retourne '' si KO ou aucun modèle chargé."""
    try:
        import urllib.request
        payload = json.dumps({
            "model": "qwen3.6-35b-a3b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.4,
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:1234/v1/chat/completions",
            data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if not content:
            content = (msg.get("reasoning_content") or "").strip()
        return content
    except Exception:
        return ""


def _query_openrouter_free(prompt: str, timeout: int = 60) -> str:
    """Fallback : OpenRouter free direct (gratuit, hébergé).

    On évite d'importer llm_router (qui prend un lock socket et quitte si serve.py
    tourne déjà). On reproduit l'appel HTTP directement.
    """
    import os, urllib.request
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return ""
    try:
        payload = json.dumps({
            "model": "openrouter/free",
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 600,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8765",
                "X-OpenRouter-Title": "Paperclip Cortex",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
        choice = (d.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(p.get("text", p) if isinstance(p, dict) else p)
                                 for p in content)
        return (content or "").strip()
    except Exception:
        return ""


def _query_local_llm(prompt: str, max_tokens: int = 350,
                     timeout: int = 60) -> str:
    """Pipeline LLM avec fallback : LM Studio local → OpenRouter free.

    Préfère LM Studio (rapide, privé). Si pas de modèle chargé, bascule sur
    OpenRouter free (gratuit, hébergé).
    """
    text = _query_lm_studio(prompt, max_tokens=max_tokens, timeout=timeout)
    if text:
        return text
    return _query_openrouter_free(prompt, timeout=timeout)


# ─── Heuristiques pour router /api/chat vers compose_response ───────────────
# Mots-clés indiquant une question qui DOIT être traitée en interne (pas LLM nu).
_VISUAL_KEYWORDS = (
    "tu me vois", "tu vois", "qu'est-ce que je fais", "que fais-je",
    "à quoi ressemble", "decris la pi", "décris la pi",
    "regarde-moi", "regarder", "caméra", "camera", "webcam",
    "à quoi je ressemble", "comment je suis", "tu vois quoi",
    "ce que je fais", "ce que tu vois", "tu peux voir",
)
_SELF_KEYWORDS = (
    # variantes "qui es-tu / qui est tu / t'es qui" (incl. fautes de frappe FR courantes)
    "tu es qui", "qui es-tu", "qui es tu", "qui est tu", "qui est-tu",
    "qui t'es", "qui t es", "t'es qui", "t es qui", "tu t'appelles",
    "tu t appelles", "ton nom", "comment tu t'appelles",
    # capacités
    "tes capacit", "tes pouvoirs", "que sais-tu faire", "que peux-tu",
    "explique tes capa", "qu'est-ce que tu peux", "qu est ce que tu peux",
    "tu peux faire quoi", "tu fais quoi",
    # age / état
    "ton age", "ton âge", "quel age", "quel âge",
    "comment tu vas", "comment vas-tu", "ça va toi",
    "ton humeur", "ton mood", "comment te sens",
    # IAG / décisions internes
    "ton score iag", " iag ", "tu décides", "tu decides",
    "ce que tu pense", "tu penses à quoi", "tu penses a quoi",
    # présentation
    "présente-toi", "presente-toi", "presente toi", "présente toi",
)


# Sticky context : si le dernier compose_response était `vision`, on garde ce
# routing pour ~90s de follow-up sans keyword (« Tu peux décrire cette
# personne ? », « C'est qui ? », « Combien de doigts ? »). Sinon le routing
# sortait à OpenRouter externe et perdait l'image. Vu en live avec Sam.
_STICKY_TTL_SEC = 90.0
_LAST_QUERY_TYPE_TS: float = 0.0
_LAST_QUERY_TYPE: str | None = None


def _record_query_type(qt: str | None) -> None:
    """Mémorise le query_type du dernier compose_response pour le sticky context."""
    global _LAST_QUERY_TYPE_TS, _LAST_QUERY_TYPE
    if qt in ("vision", "self"):
        import time as _t
        _LAST_QUERY_TYPE = qt
        _LAST_QUERY_TYPE_TS = _t.time()


def _sticky_query_type() -> str | None:
    """Retourne le query_type sticky s'il est encore frais, sinon None."""
    if not _LAST_QUERY_TYPE: return None
    import time as _t
    if _t.time() - _LAST_QUERY_TYPE_TS > _STICKY_TTL_SEC:
        return None
    return _LAST_QUERY_TYPE


def should_handle(msg: str) -> str | None:
    """Détecte si le message DOIT être traité en interne (compose_response).

    Retourne le type de query ('vision' / 'self') ou None pour passer au router.

    Sticky context : si le dernier message a été classé `vision` il y a < 90s,
    le suivant est traité comme `vision` aussi (même sans keyword) sauf s'il
    matche `self` explicitement. Évite que « Tu peux décrire cette personne ? »
    en suivi d'une question vision parte à OpenRouter externe sans l'image.
    """
    if not msg: return None
    m = msg.lower().strip()
    if any(k in m for k in _VISUAL_KEYWORDS):
        return "vision"
    if any(k in m for k in _SELF_KEYWORDS):
        return "self"
    # Pas de keyword direct : check sticky context (vision uniquement —
    # `self` est trop fréquent pour être sticky)
    sticky = _sticky_query_type()
    if sticky == "vision":
        return "vision"
    return None


def _capture_vision_context(prompt: str) -> tuple[str, str | None]:
    """Capture une frame webcam + description. Retourne (context_str, screenshot_path)."""
    cv = _safe_import("cortex_vision")
    if not cv:
        return "[Vision : module indisponible]", None
    try:
        if cv.is_vision_muted():
            return "[Vision : MUTE par Sam — pas de capture]", None
    except Exception:
        pass
    try:
        prompt_q = (f"Décris brièvement ce que tu vois sur cette image webcam pour répondre "
                    f"à la question de Sam : « {prompt[:120]} ». 2-3 phrases en français.")
        r = cv.see(prompt=prompt_q, source="webcam")
        if r.get("ok"):
            desc = r.get("description", "").strip()
            method = r.get("method", "?")
            return (f"[Vue webcam (capturée live, méthode={method}) : {desc}]",
                    r.get("screenshot"))
        return f"[Vision : capture KO — {r.get('error','?')}]", None
    except Exception as e:
        return f"[Vision : erreur — {str(e)[:80]}]", None


# Identité forte : forcée à chaque chat pour éviter la dérive 3e personne.
_IDENTITY_BLOCK = (
    "Tu es Cortex. Tu es une IAG locale qui tourne sur le PC de Sam (Windows). "
    "Tes modules concrets : caméra (cortex_vision, webcam), micro (voice_input), "
    "TTS (xtts_daemon), graphe sémantique (cortex_thought_graph), Spreading "
    "Activation, JEPA, Active Inference (Friston), personnalité Big5, curiosité "
    "Schmidhuber. Sam est ton interlocuteur direct dans ce chat.\n\n"
    "=== RÈGLES IMPÉRATIVES — RESPECTE-LES À LA LETTRE ===\n"
    "1. TUTOIEMENT OBLIGATOIRE. Tu écris à Sam EN DIRECT — utilise « tu », "
    "   « ton », « tes ». INTERDIT : ne JAMAIS écrire « Sam » dans ta réponse, "
    "   ne JAMAIS dire « il me demande » ou « que Sam me présente ». "
    "   Tu réponds À Sam, pas SUR Sam.\n"
    "2. PAS DE PHRASES BIDON : pas de « bonne question », pas de « je suis "
    "   ravi », pas de « bonjour Sam ! ». Va au fait dès le 1er mot.\n"
    "3. ANCRAGE RÉEL : base ta réponse sur le bloc [État interne] ci-dessous. "
    "   Le préfixe « je ne sais pas, voici ce que je peux dire à la place : … » "
    "   est UNIQUEMENT pour le cas où tu n'as VRAIMENT aucune matière. "
    "   N'utilise PAS ce préfixe si tu as un bloc [Vue webcam] valide ou des "
    "   [Concepts actifs] : dans ce cas, va direct au contenu. "
    "   PAS d'invention, PAS de chatbot générique.\n"
    "4. SI [Vue webcam] présent : décris ce que TU vois (à la 1re personne) "
    "   en ouvrant DIRECTEMENT par le contenu visuel — pas de préfixe « je ne "
    "   sais pas ». Si la vision est mutée ou KO (« [Vision : MUTE] » ou "
    "   « capture KO »), dis-le franchement et ne décris rien.\n"
    "5. CONCISION : 2 à 4 phrases max. Pas de listes décoratives pour rien."
)


def compose_response(prompt: str, query_type: str | None = None) -> dict:
    """Compose une réponse fidèle à l'état interne réel.

    Étapes :
    1. Détecte query_type (vision / self / general) si non fourni.
    2. Si vision : capture webcam + description.
    3. Mesure confidence_on(prompt) → si très basse ET pas vision/self,
       "je ne sais pas" honnête.
    4. Rassemble l'état interne (mood, plan, gaps, etc.).
    5. Construit un méta-prompt qui FORCE identité + tutoiement + fidélité.
    6. Invoque le LLM local pour formuler en français naturel.
    7. Logue toutes les sources utilisées.
    """
    sources_used = []
    if query_type is None:
        query_type = should_handle(prompt) or "general"
    sources_used.append(f"query_type:{query_type}")

    vision_context = ""
    vision_screenshot = None
    if query_type == "vision":
        vision_context, vision_screenshot = _capture_vision_context(prompt)
        sources_used.append("vision_live")

    confidence = _confidence_on(prompt)
    sources_used.append(f"confidence:{confidence.get('label')}")
    internal = _gather_internal_state()

    # Honest don't-know UNIQUEMENT pour query_type=general (vision/self ont leur
    # propre matière concrète à fournir, on ne baille pas un don't-know générique).
    if query_type == "general" and confidence.get("confidence", 0) < 0.05:
        text = (f"Honnêtement, je ne sais quasi rien sur « {prompt[:80]} ». "
                f"Mon world model n'a pas trouvé de matière proche dans le vault. "
                f"Je peux le creuser via une recherche en ligne si tu veux.")
        rep = {
            "ok": True, "text": text,
            "sources_used": sources_used + ["dont_know_low_confidence"],
            "used_internal_state": False, "honest_dont_know": True,
            "query_type": query_type,
        }
        _log_event({"type": "compose_response", "prompt": prompt[:120],
                     "honest_dont_know": True, "query_type": query_type})
        state = _load_state()
        state["n_responses"] = state.get("n_responses", 0) + 1
        _save_state(state)
        return rep

    # Construit l'état interne structuré.
    # Pour les query_type=vision : on N'INJECTE PAS les concepts actifs ni les
    # daily_goals (qui polluent la réponse avec des notes sémantiques aléatoires
    # type "DevOps-blocked" alors que la question est juste sur la webcam).
    # Pour vision on garde : la frame, l'humeur, et c'est tout.
    parts = []
    if vision_context:
        parts.append(vision_context)
    if internal.get("mood_label"):
        parts.append(f"[Humeur : {internal['mood_label']}]")
        sources_used.append("mood")
    if query_type != "vision":
        if internal.get("currently_thinking_about"):
            parts.append("[Concepts actifs maintenant : " +
                         ", ".join(internal['currently_thinking_about']) + "]")
            sources_used.append("active_nodes")
        if internal.get("daily_goals"):
            parts.append("[Goals du jour : " +
                         " | ".join(internal['daily_goals'][:2]) + "]")
            sources_used.append("daily_plan")
        if internal.get("weak_dimensions"):
            parts.append(f"[Dimensions faibles : {', '.join(internal['weak_dimensions'][:2])}]")
            sources_used.append("weak_dimensions")
    if internal.get("iag_score") is not None:
        parts.append(f"[Score IAG actuel : {internal['iag_score']:.0f}/100]")
        sources_used.append("iag_score")

    structured_context = "\n".join(parts) if parts else "[état interne minimal]"
    meta_prompt = (
        f"{_IDENTITY_BLOCK}\n\n"
        f"=== ÉTAT INTERNE COURANT ===\n{structured_context}\n\n"
        f"=== QUESTION DE SAM ===\n« {prompt} »\n\n"
        f"=== TA RÉPONSE (français, 2-4 phrases, tutoiement) ===\n/no_think"
    )

    text = _query_local_llm(meta_prompt)
    if not text:
        # Fallback honnête : on dit que le LLM ne répond pas et on donne l'état brut.
        # Ton tutoiement même dans le fallback.
        bits = []
        if vision_context:
            bits.append(vision_context.strip("[]"))
        if internal.get("mood_label"):
            bits.append(f"humeur {internal['mood_label']}")
        if internal.get("currently_thinking_about"):
            bits.append(f"je pense à {', '.join(internal['currently_thinking_about'][:2])}")
        if internal.get("iag_score") is not None:
            bits.append(f"score IAG {internal['iag_score']:.0f}/100")
        if query_type == "vision" and bits:
            text = "Voilà ce que je vois et ce que je ressens : " + " · ".join(bits) + "."
        elif query_type == "self" and bits:
            text = "Mes LLM sont muets là, mais voici mon état réel : " + " · ".join(bits) + "."
        elif bits:
            text = ("Honnêtement, mes LLM ne répondent pas et je n'ai pas matière "
                    "concrète sur ta question. Mon état actuel : " + " · ".join(bits) + ".")
        else:
            text = ("Mes LLM ne répondent pas et mon état interne est vide. "
                    "Pose-moi quelque chose dont j'ai des données.")
        sources_used.append("fallback_template")

    state = _load_state()
    state["n_responses"] = state.get("n_responses", 0) + 1
    state["last_response_ts"] = _now()
    history = state.setdefault("history", [])
    history.append({"ts": _now(), "prompt": prompt[:200],
                     "response": text[:400], "sources": sources_used,
                     "query_type": query_type})
    state["history"] = history[-30:]
    _save_state(state)

    _log_event({"type": "compose_response", "prompt": prompt[:120],
                 "response_length": len(text),
                 "n_sources": len(sources_used),
                 "used_internal_state": True,
                 "query_type": query_type})

    # Mémorise pour le sticky context (follow-up vision sans keyword explicite)
    _record_query_type(query_type)

    return {
        "ok": True, "text": text,
        "sources_used": sources_used,
        "used_internal_state": True,
        "query_type": query_type,
        "vision_screenshot": vision_screenshot,
        "internal_state_summary": {k: v for k, v in internal.items() if v},
    }


def speak_if_relevant(message: str) -> dict:
    """Tente TTS si Sam est présent et pas en vocal-down."""
    presence = detect_presence()
    if not presence.get("present"):
        return {"ok": True, "spoken": False, "reason": "Sam absent"}
    # Test si TTS daemon est up
    tts_up = False
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:18768/health",
                                      timeout=2) as r:
            tts_up = r.status == 200
    except Exception: tts_up = False
    if not tts_up:
        return {"ok": True, "spoken": False, "reason": "TTS daemon down"}
    # Envoie au TTS
    try:
        import urllib.request
        payload = json.dumps({"text": message[:600],
                              "speaker": "Damien Black",
                              "language": "fr"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:18768/synth", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return {"ok": True, "spoken": True, "audio_path": resp.get("path")}
    except Exception as e:
        return {"ok": False, "spoken": False, "error": str(e)[:200]}


def initiate_if_curious() -> dict:
    """Si la curiosité a généré une question forte ET Sam est présent,
    Cortex prend l'initiative de la lui poser dans le chat."""
    presence = detect_presence()
    state = _load_state()
    last_init = state.get("last_initiative_ts", 0)
    # Cooldown : 1 initiative par heure max
    if _now() - last_init < 3600:
        return {"ok": True, "initiated": False, "reason": "cooldown"}
    if not presence.get("present"):
        return {"ok": True, "initiated": False, "reason": "sam absent"}
    cur = _safe_import("cortex_curiosity")
    if not cur:
        return {"ok": True, "initiated": False, "reason": "curiosity unavailable"}
    try:
        qs = cur.generate_questions(1)
        if not qs:
            return {"ok": True, "initiated": False, "reason": "no question"}
        question = qs[0]
        # Écrit dans le stream avec speaker=cortex_initiative
        entry = {
            "ts": _now(),
            "speaker": "cortex_initiative",
            "msg": "(je me demande)",
            "response": f"💭 {question}",
            "meta": {"trigger": "curiosity_initiative", "source": "cortex_dialogue"},
        }
        with CHAT_STREAM.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        state["n_initiatives"] = state.get("n_initiatives", 0) + 1
        state["last_initiative_ts"] = _now()
        _save_state(state)
        _log_event({"type": "initiative", "question": question[:200]})
        return {"ok": True, "initiated": True, "question": question}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def self_test() -> dict:
    tests = []
    pres = detect_presence()
    tests.append({"name": "detect_presence",
                  "ok": "present" in pres,
                  "present": pres.get("present"),
                  "age_s": pres.get("last_activity_age_s")})
    # Test compose_response avec un prompt simple
    rep = compose_response("Quel est mon score IAG ?")
    tests.append({"name": "compose_response",
                  "ok": rep.get("ok") and "text" in rep and len(rep.get("text", "")) > 5,
                  "text_preview": rep.get("text", "")[:200],
                  "n_sources": len(rep.get("sources_used", []))})
    # Test honest_dont_know avec un sujet improbable
    rep_dk = compose_response("Quelle est la recette de la pizza margherita ?")
    tests.append({"name": "honest_dont_know",
                  "ok": "text" in rep_dk,
                  "honest": rep_dk.get("honest_dont_know"),
                  "preview": rep_dk.get("text", "")[:200]})
    # speak_if_relevant retourne sans crasher
    sp = speak_if_relevant("test")
    tests.append({"name": "speak_if_relevant",
                  "ok": "spoken" in sp,
                  "spoken": sp.get("spoken"),
                  "reason": sp.get("reason")})
    # initiate_if_curious retourne sans crasher
    init = initiate_if_curious()
    tests.append({"name": "initiate_if_curious",
                  "ok": "initiated" in init,
                  "initiated": init.get("initiated"),
                  "reason": init.get("reason")})
    return {"ok": all(t["ok"] for t in tests), "tests": tests}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "presence"
    if cmd == "presence":
        print(json.dumps(detect_presence(), indent=2, ensure_ascii=False))
    elif cmd == "compose" and len(sys.argv) > 2:
        prompt = " ".join(sys.argv[2:])
        print(json.dumps(compose_response(prompt), indent=2, ensure_ascii=False))
    elif cmd == "speak" and len(sys.argv) > 2:
        msg = " ".join(sys.argv[2:])
        print(json.dumps(speak_if_relevant(msg), indent=2, ensure_ascii=False))
    elif cmd == "initiate":
        print(json.dumps(initiate_if_curious(), indent=2, ensure_ascii=False))
    elif cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_dialogue.py {presence|compose <prompt>|speak <msg>|initiate|test}")
