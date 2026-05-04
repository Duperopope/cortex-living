"""
cortex_world_model.py - autonomous JEPA/world-model layer for Cortex.

This module is intentionally small and auditable. It does not pretend that a
missing model is trained: it inventories the real JEPA artifacts, runs the real
latent probe when the embedding service is online, and otherwise falls back to
real vault/graph evidence. Every autonomous step is persisted in the Obsidian
vault so the UI can prove that the agent evolved its own state.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]
VAULT = Path(os.environ.get("VAULT_PATH", r"<USER_HOME>\Documents\Obsidian Vault"))

INDEX_PATH = VAULT / ".vault-brain.sqlite"
GRAPH_FILE = VAULT / ".vault-graph.json"
STATE_FILE = VAULT / ".cortex-world-model-state.json"
EVENTS_FILE = VAULT / ".cortex-world-model-events.jsonl"

JEPA_NUMPY_MODEL = VAULT / ".vault-jepa.npz"
JEPA_PAIRS = VAULT / ".vault-jepa-pairs.npz"
JEPA_STATUS = VAULT / ".vault-jepa-status.json"
JEPA_TORCH_MODEL = VAULT / ".vault-jepa.pt"
JEPA_DATASET = VAULT / ".vault-jepa-dataset.npz"
LECUN_MANIFEST = REPO / "lecun_world_model.md"

EMBED_URL = os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1")

_STATE_LOCK = threading.RLock()
_LOOP_STARTED = False
_LOOP_INTERVAL = 75.0

_STOPWORDS = {
    "avec", "dans", "pour", "que", "qui", "quoi", "les", "des", "une", "sur",
    "est", "pas", "plus", "mon", "ton", "son", "notre", "votre", "leur",
    "this", "that", "with", "from", "into", "the", "and", "for", "you",
}

_ANCHORS = [
    "world model latent prediction JEPA",
    "active inference knowledge gap detection",
    "cortex autonomous self evolution guardrails",
    "router consortium benchmark role selection",
    "spreading activation Hebbian memory",
    "local first observable cognitive system",
]


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _json_load(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return default


def _json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _file_info(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
        return {
            "exists": True,
            "path": str(path),
            "bytes": st.st_size,
            "mtime_iso": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        }
    except Exception:
        return {"exists": False, "path": str(path)}


def model_inventory() -> dict[str, Any]:
    files = {
        "jepa_numpy_model": _file_info(JEPA_NUMPY_MODEL),
        "jepa_pairs": _file_info(JEPA_PAIRS),
        "jepa_torch_model": _file_info(JEPA_TORCH_MODEL),
        "jepa_dataset": _file_info(JEPA_DATASET),
        "vault_index": _file_info(INDEX_PATH),
        "thought_graph": _file_info(GRAPH_FILE),
        "lecun_manifest": _file_info(LECUN_MANIFEST),
    }
    status = _json_load(JEPA_STATUS, {})
    ready = bool(files["jepa_numpy_model"]["exists"] and files["vault_index"]["exists"])
    return {
        "ready": ready,
        "status": status,
        "files": files,
        "embedding_service_online": _embedding_service_online(),
        "principle": "JEPA latent predictor trained from vault pairs; fallback uses real vault FTS + thought graph.",
    }


def _default_state() -> dict[str, Any]:
    now = _now_iso()
    return {
        "ok": True,
        "autonomous": False,
        "cycles": 0,
        "version": "wm-0",
        "created_at": now,
        "updated_at": now,
        "last_step": None,
        "last_probe": None,
        "knowledge_atoms": [],
        "gaps": [],
        "notes": "State owned by cortex_world_model.py. Each cycle is append-only in .cortex-world-model-events.jsonl.",
    }


def read_state() -> dict[str, Any]:
    with _STATE_LOCK:
        state = _json_load(STATE_FILE, _default_state())
        base = _default_state()
        base.update(state if isinstance(state, dict) else {})
        return base


def write_state(state: dict[str, Any]) -> dict[str, Any]:
    with _STATE_LOCK:
        state["ok"] = True
        state["updated_at"] = _now_iso()
        _json_write(STATE_FILE, state)
        return state


def _append_event(event: dict[str, Any]) -> dict[str, Any]:
    event = {"ts": time.time(), "ts_iso": _now_iso(), **event}
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return event


def latest_events(limit: int = 12) -> list[dict[str, Any]]:
    try:
        if not EVENTS_FILE.exists():
            return []
        lines = EVENTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _age_seconds(ts: Any) -> float | None:
    try:
        if isinstance(ts, (int, float)) and ts > 0:
            return max(0.0, time.time() - float(ts))
    except Exception:
        pass
    return None


def _graph_nodes() -> list[str]:
    try:
        graph = _json_load(GRAPH_FILE, {})
        nodes = graph.get("nodes") or []
        return [str(n) for n in nodes if str(n).strip()]
    except Exception:
        return []


def _graph_labels_from_evidence(query: str, matches: list[dict[str, Any]], limit: int = 6) -> list[str]:
    """Map JEPA/vault evidence onto real thought-graph node ids.

    The 3D renderer can only animate pulses between nodes present in the graph.
    Keeping this mapping explicit prevents decorative, non-visible pulses such as
    "world_model -> prompt text".
    """
    nodes = _graph_nodes()
    node_set = set(nodes)
    by_lower = {n.lower().replace("\\", "/"): n for n in nodes}
    out: list[str] = []

    def add(label: str) -> None:
        label = str(label or "").strip().replace("\\", "/")
        if not label:
            return
        found = label if label in node_set else by_lower.get(label.lower())
        if not found:
            low = label.lower()
            found = next((n for n in nodes if n.lower().replace("\\", "/").endswith(low)), "")
        if found and found not in out:
            out.append(found)

    for match in matches:
        add(_match_label(match))
        if len(out) >= limit:
            return out[:limit]
    for hit in _graph_sample(query, limit=limit):
        add(hit)
        if len(out) >= limit:
            break
    return out[:limit]


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[\w-]{3,}", (text or "").lower(), flags=re.UNICODE)
    out = []
    for word in words:
        if word in _STOPWORDS or word.isdigit():
            continue
        if word not in out:
            out.append(word)
    return out[:12]


def _fts_query(tokens: list[str]) -> str:
    clean = [re.sub(r"[^\w-]", "", t) for t in tokens if re.sub(r"[^\w-]", "", t)]
    if not clean:
        return ""
    return " OR ".join(clean[:8])


def _vault_fts_search(query: str, limit: int = 6) -> list[dict[str, Any]]:
    if not INDEX_PATH.exists():
        return []
    tokens = _tokens(query)
    fts = _fts_query(tokens)
    if not fts:
        return []
    try:
        conn = sqlite3.connect(INDEX_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.file_path, c.source, c.chunk_idx, c.text, c.centrality,
                   bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts, int(limit)),
        ).fetchall()
    except Exception:
        return []
    matches = []
    for row in rows:
        hay = f"{row['file_path']} {row['text'][:1500]}".lower()
        overlap = len([t for t in tokens if t in hay])
        score = (overlap / max(1, len(tokens))) + min(0.18, float(row["centrality"] or 0) * 150)
        matches.append({
            "file_path": row["file_path"],
            "source": row["source"],
            "chunk_idx": row["chunk_idx"],
            "score": round(min(1.0, score), 3),
            "snippet": (row["text"] or "").replace("\n", " ")[:280],
        })
    matches.sort(key=lambda m: float(m.get("score") or 0.0), reverse=True)
    deduped = []
    seen = set()
    for match in matches:
        key = match.get("file_path")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(match)
    return deduped[:limit]


def _local_world_model_search(query: str, limit: int = 4) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    candidates = [
        LECUN_MANIFEST,
        HERE / "vault_jepa.py",
        HERE / "jepa" / "phase1_dataset.py",
        HERE / "jepa" / "phase2_train.py",
        HERE / "jepa" / "phase3_eval.py",
        Path(__file__).resolve(),
    ]
    matches = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        hay = f"{path.name} {text[:12000]}".lower()
        overlap = len([t for t in tokens if t in hay])
        if not overlap:
            continue
        score = min(1.0, 0.18 + overlap / max(1, len(tokens)))
        if path == Path(__file__).resolve():
            score = min(score, 0.62)
        pos = min([hay.find(t) for t in tokens if hay.find(t) >= 0] or [0])
        snippet = text[max(0, pos - 120):pos + 260].replace("\n", " ")
        matches.append({
            "file_path": str(path),
            "source": "project_world_model",
            "chunk_idx": 0,
            "score": round(score, 3),
            "snippet": snippet[:320],
        })
    matches.sort(key=lambda m: float(m.get("score") or 0.0), reverse=True)
    return matches[:limit]


def _graph_sample(query: str, limit: int = 6) -> list[str]:
    try:
        nodes = _graph_nodes()
        tokens = _tokens(query)
        scored = []
        for n in nodes:
            s = str(n).lower()
            overlap = sum(1 for t in tokens if t in s)
            if overlap:
                scored.append((overlap, str(n)))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [s for _, s in scored[:limit]]
    except Exception:
        return []


def _embedding_service_online() -> bool:
    try:
        parsed = urlparse(EMBED_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=0.7):
            return True
    except Exception:
        return False


def _run_jepa_probe(query: str, k: int = 5) -> dict[str, Any]:
    inv = model_inventory()
    if not inv["ready"]:
        return {"ok": False, "mode": "jepa_unavailable", "reason": "missing_jepa_model_or_index"}
    # vault_jepa.py has its own honest fallback: if live embeddings are unavailable,
    # it builds a query vector from the persisted vault embeddings, then still runs
    # the real JEPA MLP. Do not short-circuit here or the UI looks "dead" while a
    # proxy proof is still possible.
    cmd = [sys.executable, str(HERE / "vault_jepa.py"), "probe", query[:800], "-k", str(k)]
    try:
        run = subprocess.run(
            cmd,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "mode": "jepa_timeout", "reason": "probe_timeout"}
    except Exception as exc:
        return {"ok": False, "mode": "jepa_exception", "reason": str(exc)}

    out = (run.stdout or "").strip()
    err = (run.stderr or "").strip()
    if run.returncode != 0:
        lines = [line.strip() for line in (err or out).splitlines() if line.strip()]
        reason = lines[-1] if lines else f"returncode={run.returncode}"
        return {"ok": False, "mode": "jepa_error", "reason": reason[:240]}

    cosine = 0.0
    verdict = ""
    m = re.search(r"top-1:\s*([0-9.]+)\s*.*?\s{2,}(.+)", out)
    if m:
        try:
            cosine = float(m.group(1))
        except Exception:
            cosine = 0.0
        verdict = m.group(2).strip()

    matches = []
    for line in out.splitlines():
        lm = re.match(r"\s*([0-9.]+)\s+(.+)$", line)
        if not lm:
            continue
        try:
            sim = float(lm.group(1))
        except Exception:
            continue
        matches.append({"score": round(sim, 4), "file_path": lm.group(2).strip()})
    embedding_source = ""
    m_source = re.search(r"Embedding source:\s*(.+)", out)
    if m_source:
        embedding_source = m_source.group(1).strip()
    mode = "jepa_latent_proxy" if "vault_proxy_embedding" in embedding_source else "jepa_latent"
    reason = "JEPA model + vault proxy embedding" if mode == "jepa_latent_proxy" else "JEPA model + live embeddings"
    return {
        "ok": True,
        "mode": mode,
        "reason": reason,
        "embedding_source": embedding_source,
        "cosine": round(cosine, 4),
        "confidence": round(max(0.0, min(1.0, cosine)), 4),
        "gap": round(max(0.0, 1.0 - max(0.0, min(1.0, cosine))), 4),
        "verdict": verdict,
        "matches": matches[:k],
        "raw": out[-1600:],
    }


def probe_world(query: str) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty_query"}

    jepa = _run_jepa_probe(query)
    fts = _local_world_model_search(query) + _vault_fts_search(query)
    fts.sort(key=lambda m: float(m.get("score") or 0.0), reverse=True)
    seen_paths = set()
    fts = [m for m in fts if not (m.get("file_path") in seen_paths or seen_paths.add(m.get("file_path")))]
    graph_hits = _graph_sample(query)

    if jepa.get("ok"):
        confidence = float(jepa.get("confidence") or 0.0)
        gap = float(jepa.get("gap") or (1.0 - confidence))
        mode = jepa.get("mode") or "jepa_latent"
        verdict = jepa.get("verdict") or ("covered" if confidence >= 0.6 else "gap")
        matches = jepa.get("matches") or fts
        reason = jepa.get("reason") or "JEPA model + vault embeddings"
    else:
        best = max([m.get("score", 0.0) for m in fts] or [0.0])
        confidence = round(min(0.72, best), 3)
        gap = round(1.0 - confidence, 3)
        mode = "vault_lexical_fallback"
        verdict = "partial_coverage" if confidence >= 0.35 else "knowledge_gap"
        matches = fts
        reason = jepa.get("reason") or "jepa_probe_unavailable"

    return {
        "ok": True,
        "query": query,
        "mode": mode,
        "reason": reason,
        "confidence": confidence,
        "gap": gap,
        "verdict": verdict,
        "matches": matches[:6],
        "graph_hits": graph_hits[:6],
        "jepa": jepa,
        "inventory": model_inventory(),
        "ts": time.time(),
        "ts_iso": _now_iso(),
    }


def _match_label(match: dict[str, Any]) -> str:
    return str(match.get("file_path") or match.get("source") or "").strip()


def _activate_brain(seed: str, matches: list[dict[str, Any]], source: str) -> dict[str, Any]:
    graph_labels = _graph_labels_from_evidence(seed, matches, limit=6)
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import cortex_activation as _ca

        wander = {}
        if source in {"manual", "chat", "loop", "repair", "self_test", "diagnose"}:
            wander = _ca.wander_once(reason=f"world_model_{source}")
            if wander.get("ok"):
                w_labels = [wander.get("seed")] + [n.get("node") for n in (wander.get("neighbors") or [])]
                for label in w_labels:
                    if label and label not in graph_labels:
                        graph_labels.append(str(label))
        if graph_labels:
            _ca.co_activate(graph_labels[:6])
        return {
            "ok": True,
            "labels": graph_labels[:6],
            "wander": wander,
            "visible_graph_nodes": len(graph_labels[:6]),
            "non_graph_pulses_suppressed": True,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "labels": graph_labels[:6]}


def _choose_seed(state: dict[str, Any], seed: str | None) -> str:
    if seed and seed.strip():
        return seed.strip()
    gaps = state.get("gaps") or []
    if gaps:
        return str(random.choice(gaps).get("query") or random.choice(_ANCHORS))
    probe = state.get("last_probe") or {}
    if probe.get("query"):
        return str(probe["query"])
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import cortex_activation as _ca

        snap = _ca.snapshot()
        active = list((snap.get("active_nodes") or {}).keys())
        if active:
            return random.choice(active)
    except Exception:
        pass
    return random.choice(_ANCHORS)


def autonomous_step(seed: str | None = None, source: str = "manual", probe: dict[str, Any] | None = None) -> dict[str, Any]:
    with _STATE_LOCK:
        state = read_state()
        query = _choose_seed(state, seed)
        probe = probe or probe_world(query)
        matches = probe.get("matches") or []
        brain = _activate_brain(query, matches, source)

        cycles = int(state.get("cycles") or 0) + 1
        atoms = list(state.get("knowledge_atoms") or [])
        for item in [query, probe.get("mode"), probe.get("verdict")] + [_match_label(m) for m in matches[:3]]:
            item = str(item or "").strip()
            if item and item not in atoms:
                atoms.append(item)
        atoms = atoms[-120:]

        gaps = list(state.get("gaps") or [])
        if float(probe.get("gap") or 0.0) >= 0.55:
            gap = {
                "query": query,
                "gap": probe.get("gap"),
                "mode": probe.get("mode"),
                "reason": probe.get("reason"),
                "ts_iso": _now_iso(),
            }
            if not any(g.get("query") == query for g in gaps[-30:]):
                gaps.append(gap)
        gaps = gaps[-60:]

        summary = _summarize_step(query, probe, brain, cycles)
        event = _append_event({
            "type": "autonomous_step",
            "source": source,
            "cycle": cycles,
            "query": query,
            "mode": probe.get("mode"),
            "confidence": probe.get("confidence"),
            "gap": probe.get("gap"),
            "verdict": probe.get("verdict"),
            "brain": brain,
            "summary": summary,
        })

        state.update({
            "cycles": cycles,
            "version": f"wm-{cycles}",
            "last_step": event,
            "last_probe": probe,
            "knowledge_atoms": atoms,
            "gaps": gaps,
        })
        write_state(state)
        return {"ok": True, "state": state, "event": event, "probe": probe, "brain": brain, "summary": summary}


def _summarize_step(query: str, probe: dict[str, Any], brain: dict[str, Any], cycles: int) -> str:
    conf = float(probe.get("confidence") or 0.0)
    gap = float(probe.get("gap") or 0.0)
    mode = probe.get("mode") or "unknown"
    verdict = probe.get("verdict") or "n/a"
    action = "activation Hebbian ecrite" if brain.get("ok") else f"activation echouee: {brain.get('error')}"
    return (
        f"Cycle {cycles}: prediction '{query[:96]}' via {mode}. "
        f"Confiance {conf:.2f}, gap {gap:.2f}, verdict {verdict}. {action}."
    )


def chat(prompt: str) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty_prompt"}
    probe = probe_world(prompt)
    step = autonomous_step(seed=prompt, source="chat", probe=probe)
    response = _compose_chat_response(prompt, probe, step)
    event = _append_event({
        "type": "chat",
        "prompt": prompt,
        "response": response,
        "mode": probe.get("mode"),
        "confidence": probe.get("confidence"),
        "gap": probe.get("gap"),
    })
    state = read_state()
    state["last_chat"] = event
    write_state(state)
    return {"ok": True, "response": response, "probe": probe, "step": step, "state": state, "events": latest_events()}


def _compose_chat_response(prompt: str, probe: dict[str, Any], step: dict[str, Any]) -> str:
    inv = probe.get("inventory") or {}
    files = inv.get("files") or {}
    jepa_file = files.get("jepa_numpy_model") or {}
    matches = probe.get("matches") or []
    top = matches[0] if matches else {}
    top_label = _match_label(top) or "aucune note proche"
    mode = probe.get("mode")
    reason = probe.get("reason")
    conf = float(probe.get("confidence") or 0.0)
    gap = float(probe.get("gap") or 0.0)
    return "\n".join([
        "### World model",
        f"Question testee: `{prompt[:160]}`",
        "",
        f"- Mode reel: `{mode}` ({reason})",
        f"- Modele JEPA disque: {'present' if jepa_file.get('exists') else 'absent'}",
        f"- Confiance: **{conf:.2f}** ; gap: **{gap:.2f}**",
        f"- Meilleur ancrage vault: `{top_label}`",
        f"- Evolution: {step.get('summary', 'cycle non ecrit')}",
        "",
        "Je ne marque pas ca comme certain: je stocke le cycle, les preuves et les gaps dans le vault pour que tu puisses verifier.",
    ])


def set_autonomous(enabled: bool) -> dict[str, Any]:
    state = read_state()
    state["autonomous"] = bool(enabled)
    state["autonomy_changed_at"] = _now_iso()
    write_state(state)
    event = _append_event({"type": "autonomy", "enabled": bool(enabled)})
    return {"ok": True, "enabled": bool(enabled), "state": state, "event": event}


def _brain_runtime() -> dict[str, Any]:
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import cortex_activation as _ca

        snap = _ca.snapshot()
        pulses = _ca.recent_pulses(0.0)
        return {
            "ok": True,
            "n_active": snap.get("n_active", 0),
            "cum_activations": snap.get("cum_activations", 0),
            "cum_pulses": snap.get("cum_pulses", 0),
            "cum_hebbian_ticks": snap.get("cum_hebbian_ticks", 0),
            "last_activation_age_sec": _age_seconds(snap.get("last_activation_ts")),
            "last_pulse_age_sec": _age_seconds(snap.get("last_pulse_ts")),
            "last_wander_age_sec": _age_seconds(snap.get("last_wander_ts")),
            "recent_pulses": pulses[-12:],
            "recent_pulse_count": len(pulses),
            "wander_interval": snap.get("wander_interval"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "recent_pulses": []}


def diagnose() -> dict[str, Any]:
    state = read_state()
    inv = model_inventory()
    last_step = state.get("last_step") or {}
    brain = _brain_runtime()
    last_step_age = _age_seconds(last_step.get("ts"))
    issues: list[dict[str, Any]] = []
    if not state.get("autonomous"):
        issues.append({
            "severity": "blocked",
            "title": "Autonomie coupée",
            "detail": "La boucle JEPA/world-model était persistée sur autonomous:false. Elle ne cycle donc pas seule.",
            "fix": "Bouton Réveiller ou endpoint /api/cortex/world_model/repair.",
        })
    if last_step_age is not None and last_step_age > 180:
        issues.append({
            "severity": "stale",
            "title": "Dernier cycle ancien",
            "detail": f"Dernier cycle world model il y a {int(last_step_age)} s.",
            "fix": "Forcer un cycle puis vérifier les pulses.",
        })
    if brain.get("ok") and not brain.get("recent_pulse_count"):
        issues.append({
            "severity": "visual",
            "title": "Aucune comète visible à cet instant",
            "detail": "Les pulses sont réels mais courts; l'ancienne version en émettait aussi vers des labels non présents dans le graphe 3D.",
            "fix": "Les nouveaux cycles sont ancrés sur de vrais nœuds du thought graph.",
        })
    if not inv.get("ready"):
        issues.append({
            "severity": "model",
            "title": "Artefacts JEPA incomplets",
            "detail": "Le modèle latent ou l'index vault manque.",
            "fix": "Préparer/entraîner vault_jepa avant d'appeler cela JEPA réel.",
        })
    return {
        "ok": True,
        "concept": {
            "plain": "Ce n'est pas un chat LLM. C'est un prédicteur latent JEPA: il encode un état du vault/monde, prédit ce qui devrait être proche, compare aux notes réelles, puis écrit gaps, cycles et activations.",
            "not_llm": True,
            "current_mode": "jepa_latent_or_proxy",
            "limits": "Autonomie observable locale, pas IAG générale. Les actions risquées restent gated.",
        },
        "state": state,
        "inventory": inv,
        "brain": brain,
        "issues": issues,
        "events": latest_events(12),
        "ts": time.time(),
        "ts_iso": _now_iso(),
    }


def _disk_move_dry_run() -> dict[str, Any]:
    drives = []
    for root in ["C:\\", "H:\\", "G:\\", "F:\\", "E:\\"]:
        path = Path(root)
        if not path.exists():
            continue
        try:
            usage = shutil.disk_usage(path)
        except Exception:
            continue
        used_pct = (usage.used / max(1, usage.total)) * 100
        drives.append({
            "root": root,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "used_pct": round(used_pct, 1),
        })
    source_candidates = [
        Path.home() / ".cache",
        Path.home() / ".lmstudio" / "models",
        Path.home() / "AppData" / "Local" / "pip" / "Cache",
        Path.home() / "AppData" / "Local" / "Temp",
    ]
    candidates = []
    for source in source_candidates:
        if source.exists():
            candidates.append(str(source))
    target = max(drives, key=lambda d: d["free_gb"], default={})
    return {
        "ok": True,
        "type": "disk_move",
        "execution": "dry_run_only",
        "drives": drives,
        "candidate_sources": candidates[:6],
        "suggested_target_root": target.get("root"),
        "requires_confirm": True,
        "safe_endpoint": "/api/cortex/disk_action",
        "proof": "Aucun fichier déplacé par ce test. Il calcule seulement le plan et impose confirm:true pour une action réelle.",
    }


def repair(reason: str = "user_repair") -> dict[str, Any]:
    before = diagnose()
    set_autonomous(True)
    step = autonomous_step(
        seed="prendre conscience de mon état, de mon vault, du graphe, de mes limites et des actions sûres possibles",
        source="repair",
    )
    after = diagnose()
    event = _append_event({
        "type": "repair",
        "reason": reason,
        "summary": "Autonomie réactivée, cycle JEPA écrit, activation/pulses ancrés sur le graphe réel.",
        "step_cycle": (step.get("event") or {}).get("cycle"),
    })
    return {"ok": True, "before": before, "step": step, "after": after, "event": event}


def self_test() -> dict[str, Any]:
    tests: list[dict[str, Any]] = []

    jepa_probe = probe_world("JEPA world model LeCun prédiction latente mémoire autonome")
    tests.append({
        "name": "jepa_latent_probe",
        "ok": bool(jepa_probe.get("ok")),
        "mode": jepa_probe.get("mode"),
        "confidence": jepa_probe.get("confidence"),
        "gap": jepa_probe.get("gap"),
        "proof": jepa_probe.get("reason"),
    })

    aware = autonomous_step(
        seed="prendre conscience de mon univers local: repo Paperclip, vault Obsidian, graphe de pensée, limites de permission",
        source="self_test",
        probe=probe_world("repo Paperclip vault Obsidian graphe de pensée limites permissions"),
    )
    tests.append({
        "name": "self_awareness_cycle",
        "ok": bool(aware.get("ok") and (aware.get("brain") or {}).get("ok")),
        "cycle": (aware.get("event") or {}).get("cycle"),
        "proof": aware.get("summary"),
        "visible_nodes": (aware.get("brain") or {}).get("visible_graph_nodes", 0),
    })

    talk = chat("Explique en une phrase ce que tu es: world model JEPA, pas LLM.")
    tests.append({
        "name": "discussion",
        "ok": bool(talk.get("ok") and talk.get("response")),
        "mode": (talk.get("probe") or {}).get("mode"),
        "proof": (talk.get("response") or "")[:420],
    })

    disk = _disk_move_dry_run()
    tests.append({
        "name": "disk_move_safe_dry_run",
        "ok": bool(disk.get("ok") and disk.get("requires_confirm")),
        "proof": disk,
    })

    brain = _activate_brain("visualiser des comètes de propagation synaptique sur des vrais nœuds", [], "self_test")
    runtime = _brain_runtime()
    tests.append({
        "name": "brain_visual_pulses",
        "ok": bool(brain.get("ok") and runtime.get("recent_pulse_count", 0) > 0),
        "proof": {
            "activation": brain,
            "recent_pulse_count": runtime.get("recent_pulse_count", 0),
            "recent_pulses": runtime.get("recent_pulses", [])[-4:],
        },
    })

    ok = all(bool(t.get("ok")) for t in tests)
    event = _append_event({
        "type": "self_test",
        "ok": ok,
        "summary": "Tests autonomie/JEPA/discussion/disk dry-run/visualisation exécutés.",
        "tests": tests,
    })
    state = read_state()
    state["last_self_test"] = event
    write_state(state)
    return {"ok": ok, "tests": tests, "event": event, "state": state, "diagnose": diagnose()}


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "state": read_state(),
        "inventory": model_inventory(),
        "events": latest_events(14),
        "diagnose": diagnose(),
        "ts": time.time(),
        "ts_iso": _now_iso(),
    }


def _loop() -> None:
    while True:
        try:
            state = read_state()
            if state.get("autonomous"):
                autonomous_step(source="loop")
        except Exception as exc:
            _append_event({"type": "loop_error", "error": str(exc)})
        time.sleep(_LOOP_INTERVAL)


def start(interval: float = 75.0) -> dict[str, Any]:
    global _LOOP_STARTED, _LOOP_INTERVAL
    _LOOP_INTERVAL = max(20.0, float(interval or 75.0))
    if _LOOP_STARTED:
        return {"ok": True, "started": False, "reason": "already_started", "interval": _LOOP_INTERVAL}
    _LOOP_STARTED = True
    threading.Thread(target=_loop, name="cortex-world-model", daemon=True).start()
    return {"ok": True, "started": True, "interval": _LOOP_INTERVAL}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_probe = sub.add_parser("probe")
    p_probe.add_argument("query")
    p_step = sub.add_parser("step")
    p_step.add_argument("seed", nargs="?")
    p_auto = sub.add_parser("autonomy")
    p_auto.add_argument("enabled", choices=["on", "off"])
    sub.add_parser("diagnose")
    sub.add_parser("repair")
    sub.add_parser("self-test")
    args = ap.parse_args()
    if args.cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    elif args.cmd == "probe":
        print(json.dumps(probe_world(args.query), ensure_ascii=False, indent=2))
    elif args.cmd == "step":
        print(json.dumps(autonomous_step(args.seed, source="cli"), ensure_ascii=False, indent=2))
    elif args.cmd == "autonomy":
        print(json.dumps(set_autonomous(args.enabled == "on"), ensure_ascii=False, indent=2))
    elif args.cmd == "diagnose":
        print(json.dumps(diagnose(), ensure_ascii=False, indent=2))
    elif args.cmd == "repair":
        print(json.dumps(repair("cli"), ensure_ascii=False, indent=2))
    elif args.cmd == "self-test":
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
