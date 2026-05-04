"""
cortex_skills.py — Cortex cherche des skills (packages Python) sur PyPI et évalue leur pertinence.

Approche émergente : Cortex définit un besoin → cherche → évalue → propose à Sam.
Jamais d'install automatique : safety = humain valide via /api/cortex/skills/install.

Sources :
- PyPI search API (https://pypi.org/simple/) : liste tous les packages publics
- GitHub Code Search via web (sans API key, lourd)
- Awesome Lists (curated par humains, qualité haute)

Le pipeline :
1. Cortex génère une "needed capability" (ex: "détection d'émotions vocales")
2. recherche PyPI pour mots-clés
3. lit les top descriptions
4. évalue via LLM router : pertinent ? populaire ? actif ?
5. shortlist écrite dans .cortex-skill-candidates.json
6. Sam valide → install via subprocess (pip in venv-xtts ou main)

Skills déjà actifs dans Cortex (registre interne) :
- voice : whisper, pyaudio, edge_tts, piper, TTS (xtts)
- vision : cv2, mss, PIL, pytesseract
- ml : sklearn, numpy, sentence-transformers
- system : psutil, mss
- web : urllib, requests
"""
import datetime as dt
import json
import re
import urllib.request
import urllib.parse
from pathlib import Path

REPO = Path(r"<CORTEX_REPO>")
CANDIDATES_FILE = REPO / "scripts" / "brain" / ".cortex-skill-candidates.json"
INSTALLED_REGISTRY = REPO / "scripts" / "brain" / "cortex_skills_installed.json"

# Skills déjà connus de Cortex (mis à jour à chaque install validée)
DEFAULT_INSTALLED = {
    "voice": ["faster-whisper", "pyaudio", "edge-tts", "piper-tts", "TTS"],
    "vision": ["opencv-python", "mss", "Pillow", "pytesseract"],
    "ml": ["scikit-learn", "numpy", "torch"],
    "system": ["psutil"],
    "web": ["urllib3", "requests"],
}


def installed_skills() -> dict:
    if INSTALLED_REGISTRY.exists():
        try: return json.loads(INSTALLED_REGISTRY.read_text(encoding="utf-8"))
        except: pass
    return dict(DEFAULT_INSTALLED)


def search_pypi(query: str, limit: int = 10) -> list[dict]:
    """Search PyPI via JSON API. Returns list of {name, summary, version}."""
    try:
        # PyPI's actual JSON search isn't well documented, use the web search HTML scraping
        url = f"https://pypi.org/search/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Cortex/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
        # Parser HTML léger pour extraire les packages
        results = []
        # Pattern : <a class="package-snippet" href="/project/{name}/">
        pkg_pattern = re.compile(
            r'<a class="package-snippet" href="/project/([^/]+)/[^"]*"[^>]*>.*?'
            r'<span class="package-snippet__name">([^<]+)</span>.*?'
            r'<span class="package-snippet__version">([^<]+)</span>.*?'
            r'<p class="package-snippet__description">([^<]*)</p>',
            re.DOTALL
        )
        for m in pkg_pattern.finditer(html):
            results.append({
                "name": m.group(2).strip(),
                "version": m.group(3).strip(),
                "summary": m.group(4).strip(),
                "url": f"https://pypi.org/project/{m.group(1)}/",
            })
            if len(results) >= limit: break
        return results
    except Exception as e:
        return [{"error": str(e)}]


def evaluate_skill(name: str, summary: str, need: str) -> dict:
    """Demande au router (free model) si ce package est pertinent vu le besoin."""
    import subprocess
    OPENCODE = r"<USER_HOME>\AppData\Roaming\npm\opencode.cmd"
    prompt = (
        f"Tu es Cortex. Tu évalues si un package Python répond à ton besoin.\n\n"
        f"Besoin : {need}\n\n"
        f"Package : {name} (v?)\n"
        f"Description : {summary}\n\n"
        f"Réponds UNIQUEMENT avec un JSON :\n"
        f"{{\"relevant\": true|false, \"score\": 0-10, \"reason\": \"1 phrase\"}}"
    )
    try:
        r = subprocess.run([OPENCODE, "run", "--model", "opencode/minimax-m2.5-free", "-"],
                           input=prompt, capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        out = r.stdout
        m = re.search(r'\{[^{}]+\}', out)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        return {"relevant": False, "score": 0, "reason": f"err: {e}"}
    return {"relevant": False, "score": 0, "reason": "no parse"}


def discover(need: str, limit: int = 5) -> dict:
    """Pipeline complet : search → evaluate → save shortlist."""
    found = search_pypi(need, limit=10)
    if not found or "error" in found[0]:
        return {"ok": False, "error": found[0].get("error", "no results")}
    candidates = []
    already_installed = set()
    for cat, pkgs in installed_skills().items():
        already_installed.update([p.lower() for p in pkgs])
    for pkg in found[:limit]:
        if pkg["name"].lower() in already_installed:
            continue
        evaluation = evaluate_skill(pkg["name"], pkg["summary"], need)
        candidates.append({**pkg, "evaluation": evaluation})
    # Tri par score
    candidates.sort(key=lambda x: -(x["evaluation"].get("score", 0)))
    # Save shortlist
    shortlist = {
        "need": need,
        "ts": dt.datetime.now().isoformat(timespec='seconds'),
        "candidates": candidates,
    }
    try:
        existing = []
        if CANDIDATES_FILE.exists():
            existing = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
            if not isinstance(existing, list): existing = []
        existing.append(shortlist)
        existing = existing[-20:]  # garde les 20 dernières recherches
        CANDIDATES_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception: pass
    return {"ok": True, "candidates": candidates, "saved": str(CANDIDATES_FILE)}


def list_candidates() -> list[dict]:
    """Lit la shortlist actuelle de skills proposés."""
    if not CANDIDATES_FILE.exists(): return []
    try:
        data = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except: return []


def install_skill(package_name: str, target_env: str = "main") -> dict:
    """Installe un package validé. target_env: 'main' ou 'venv-xtts'."""
    import subprocess
    pip_cmd = ["pip", "install", package_name]
    if target_env == "venv-xtts":
        pip_cmd = [str(REPO / ".venv-xtts" / "Scripts" / "pip.exe"), "install", package_name]
    try:
        r = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        success = r.returncode == 0
        if success:
            # Update registry
            reg = installed_skills()
            reg.setdefault("custom", []).append(package_name)
            INSTALLED_REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": success, "package": package_name, "env": target_env,
            "stdout": r.stdout[-500:], "stderr": r.stderr[-500:] if r.stderr else "",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: cortex_skills.py search '<need>' | candidates | install <pkg>")
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "search":
        need = " ".join(sys.argv[2:])
        print(json.dumps(discover(need), ensure_ascii=False, indent=2))
    elif cmd == "candidates":
        print(json.dumps(list_candidates(), ensure_ascii=False, indent=2))
    elif cmd == "install":
        print(json.dumps(install_skill(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "installed":
        print(json.dumps(installed_skills(), ensure_ascii=False, indent=2))
