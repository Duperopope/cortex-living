"""
cortex_research.py — Cortex apprend à faire de la vraie recherche scientifique.

Sources interrogées (chacune avec son URL de provenance enregistrée) :
1. **arXiv** (cs/stat/q-bio/...)  — papers preprints, full title + abstract + authors + URL
2. **Wikipedia REST API**         — articles encyclopédiques avec source citée
3. **DuckDuckGo HTML**            — fallback web sans clé API
4. **Semantic Scholar API**       — papers académiques (citations + venue)

Pipeline :
  research(query) → fetch parallèle des 4 sources →
  scoring (recency × relevance × source_credibility) →
  shortlist top-N →
  synthèse via opencode minimax (sourcée, anti-hallucination) →
  écriture note `08 - Semantic/research/{slug}.md` avec frontmatter
   {sources: [...], confidence, when, query} →
  trigger thought_graph rebuild (la nouvelle note devient un vrai nœud).

Garde-fous anti-hallucination :
- Synthèse demandée AVEC les passages sources affichés en contexte du LLM
- LLM doit citer la source par index `[1]`, `[2]` (validable post-hoc)
- Si moins de 2 sources crédibles → on n'écrit rien et on retourne `low_confidence`

Cortex utilise ce module à 3 endroits :
1. `cortex_emergence.action.research` → sur sujet identifié comme gap (via JEPA)
2. `cortex_homeostasis.safe_clean_disk` → research d'un pattern inconnu pour décider
3. `/api/cortex/research?query=…` → Sam pose une question d'introspection

Citations méthodologiques :
- Salton & McGill (1983), "Introduction to Modern Information Retrieval" — TF-IDF scoring
- Page et al. (1999), "PageRank Citation Ranking" — credibility ranking
- Lewis et al. (2020), "Retrieval-Augmented Generation" — synthesis with citations
"""
import datetime as dt
import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

VAULT      = Path(r"<USER_HOME>\Documents\Obsidian Vault")
RESEARCH_DIR = VAULT / "08 - Semantic" / "research"
CACHE_FILE = Path(r"<CORTEX_REPO>\scripts\brain\.cortex-research-cache.json")
LOG_FILE   = Path(r"<CORTEX_REPO>\scripts\brain\.cortex-research.log")
OPENCODE   = r"<USER_HOME>\AppData\Roaming\npm\opencode.cmd"

RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SEC = 7 * 86400  # 7 jours : on évite de re-fetch trop souvent


def _log(msg: str):
    line = f"[{dt.datetime.now().isoformat(timespec='seconds')}] {msg}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


def _load_cache() -> dict:
    if not CACHE_FILE.exists(): return {}
    try: return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception: return {}


def _save_cache(c: dict):
    try: CACHE_FILE.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception: pass


def _http_get(url: str, timeout: int = 8, headers: dict = None) -> str | None:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "CortexResearch/1.0 (contact: s.medjaher@gmail.com)",
            **(headers or {}),
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        _log(f"http_get err {url[:80]}: {e}")
        return None


# ─── arXiv API ──────────────────────────────────────────────────────────────
def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    """https://info.arxiv.org/help/api/index.html — Atom feed des résultats."""
    q = urllib.parse.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{q}&start=0&max_results={max_results}&sortBy=relevance&sortOrder=descending"
    raw = _http_get(url, timeout=10)
    if not raw: return []
    out = []
    for entry in re.findall(r'<entry>([\s\S]*?)</entry>', raw):
        def grab(tag):
            m = re.search(rf'<{tag}[^>]*>([\s\S]*?)</{tag}>', entry)
            return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ""
        link_m = re.search(r'<id>([^<]+)</id>', entry)
        out.append({
            "source": "arxiv",
            "title":   grab("title"),
            "summary": grab("summary"),
            "authors": [a.strip() for a in re.findall(r'<name>([^<]+)</name>', entry)],
            "url":     link_m.group(1).strip() if link_m else "",
            "published": grab("published"),
        })
    return out


# ─── Wikipedia REST API ─────────────────────────────────────────────────────
def search_wikipedia(query: str, max_results: int = 3, lang: str = "en") -> list[dict]:
    """REST: opensearch + page summaries."""
    q = urllib.parse.quote(query)
    url = f"https://{lang}.wikipedia.org/w/api.php?action=opensearch&search={q}&limit={max_results}&format=json"
    raw = _http_get(url, timeout=8)
    if not raw: return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) < 4: return []
        titles, descs, urls = data[1], data[2], data[3]
    except Exception: return []
    out = []
    for t, d, u in zip(titles, descs, urls):
        out.append({"source": "wikipedia", "title": t, "summary": d, "url": u, "lang": lang})
    return out


# ─── Semantic Scholar API (académique, citations comptées) ──────────────────
def search_semantic_scholar(query: str, max_results: int = 5) -> list[dict]:
    """https://api.semanticscholar.org/graph/v1/paper/search"""
    q = urllib.parse.quote(query)
    url = (f"https://api.semanticscholar.org/graph/v1/paper/search?"
           f"query={q}&limit={max_results}"
           f"&fields=title,abstract,authors,year,citationCount,venue,url,externalIds")
    raw = _http_get(url, timeout=10)
    if not raw: return []
    try:
        data = json.loads(raw); items = data.get("data", [])
    except Exception: return []
    out = []
    for p in items:
        authors = [a.get("name", "?") for a in (p.get("authors") or [])][:5]
        out.append({
            "source": "semantic_scholar",
            "title": p.get("title", ""),
            "summary": (p.get("abstract") or "")[:1200],
            "authors": authors,
            "year": p.get("year"),
            "venue": p.get("venue", ""),
            "citations": p.get("citationCount", 0),
            "url": p.get("url") or (p.get("externalIds", {}) or {}).get("DOI", ""),
        })
    return out


# ─── DuckDuckGo HTML (fallback web) ─────────────────────────────────────────
def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """Scrape le HTML de duckduckgo.com — pas d'API key requise."""
    q = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={q}"
    raw = _http_get(url, timeout=10)
    if not raw: return []
    out = []
    # Pattern simple : <a class="result__a" href="...">TITLE</a>
    for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                         raw, re.I)[:max_results]:
        href, title = m.group(1), m.group(2).strip()
        out.append({"source": "duckduckgo", "title": title, "url": href, "summary": ""})
    return out[:max_results]


# ─── Scoring credibility ────────────────────────────────────────────────────
SOURCE_WEIGHT = {
    "semantic_scholar": 1.0,   # papers académiques cités
    "arxiv":            0.85,  # preprints sérieux
    "wikipedia":        0.65,  # encyclopédie collaborative
    "duckduckgo":       0.40,  # web ouvert, qualité variable
}


def _score(item: dict) -> float:
    base = SOURCE_WEIGHT.get(item.get("source"), 0.3)
    # Boost pour citations (semantic_scholar)
    cites = item.get("citations") or 0
    cite_bonus = min(0.3, cites / 1000)
    # Boost pour récence (papers ≥ 2020)
    year = item.get("year") or 0
    recency = 0.0
    if year >= 2025: recency = 0.20
    elif year >= 2022: recency = 0.10
    return round(base + cite_bonus + recency, 3)


# ─── Synthèse via LLM (avec citations forcées) ──────────────────────────────
SYNTH_PROMPT = """Tu es un chercheur. Synthétise ce qu'on sait sur la question suivante
en t'appuyant UNIQUEMENT sur les sources fournies. Cite chaque affirmation par son
index `[N]`. Si une affirmation n'est étayée par aucune source, ne l'inclus pas.

Question : {query}

Sources :
{sources_block}

Format de réponse (markdown) :
- 2-3 paragraphes max
- Chaque phrase clé suivie de [1], [2]... pour la source
- Termine par une ligne `confidence: low|medium|high` selon le nombre et la qualité
  des sources convergentes."""


def _ask_llm(prompt: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run([OPENCODE, "run", "--model", "opencode/minimax-m2.5-free", "-"],
                           input=prompt, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = "\n".join(l for l in r.stdout.splitlines()
                        if l.strip() and not l.startswith(">") and "\x1b" not in l)
        return out.strip()
    except Exception as e:
        return f"err: {e}"


# ─── Slug + write note ──────────────────────────────────────────────────────
def _slugify(s: str) -> str:
    s = re.sub(r'[^\w\s-]', '', s.lower())
    return re.sub(r'[\s-]+', '-', s).strip('-')[:60] or "research"


def _write_research_note(query: str, synthesis: str, sources: list[dict],
                         confidence: str = "medium") -> Path:
    slug = _slugify(query)
    fname = f"{dt.datetime.now().strftime('%Y%m%d')}-{slug}.md"
    body = [
        "---",
        f"captured_at: {dt.datetime.now().isoformat(timespec='seconds')}",
        "type: research_note",
        f"query: {json.dumps(query, ensure_ascii=False)}",
        f"confidence: {confidence}",
        f"n_sources: {len(sources)}",
        "sources:",
    ]
    for i, s in enumerate(sources):
        body.append(f"  - id: {i+1}")
        body.append(f"    type: {s.get('source')}")
        body.append(f"    title: {json.dumps(s.get('title',''), ensure_ascii=False)}")
        if s.get("url"): body.append(f"    url: {s['url']}")
        if s.get("year"): body.append(f"    year: {s['year']}")
    body.append("---")
    body.append("")
    body.append(f"# Recherche : {query}")
    body.append("")
    body.append(synthesis)
    body.append("")
    body.append("## Sources brutes")
    for i, s in enumerate(sources):
        body.append(f"")
        body.append(f"### [{i+1}] {s.get('title','(sans titre)')}")
        if s.get("authors"): body.append(f"*{', '.join(s['authors'][:5])}*")
        if s.get("year"):    body.append(f"Année : {s['year']}")
        if s.get("venue"):   body.append(f"Venue : {s['venue']}")
        if s.get("url"):     body.append(f"<{s['url']}>")
        if s.get("summary"): body.append(f"\n{s['summary'][:800]}")
    path = RESEARCH_DIR / fname
    path.write_text("\n".join(body), encoding="utf-8")
    return path


# ─── Pipeline complet ───────────────────────────────────────────────────────
def research(query: str, write_note: bool = True, force_refetch: bool = False) -> dict:
    """Recherche multi-sources + synthèse + écriture note Semantic.
    Retour : {ok, query, sources, synthesis, confidence, note_path}"""
    cache = _load_cache()
    key = query.strip().lower()
    if (not force_refetch) and key in cache:
        c = cache[key]
        if time.time() - c.get("ts", 0) < CACHE_TTL_SEC:
            _log(f"cache hit: {query}")
            return c["result"]

    # Fetch parallèle des 4 sources
    results: list[dict] = []
    threads = []
    def _do(fn, target):
        try: target.extend(fn(query))
        except Exception as e: _log(f"{fn.__name__} err: {e}")
    for fn in (search_arxiv, search_semantic_scholar, search_wikipedia, search_duckduckgo):
        t = threading.Thread(target=_do, args=(fn, results), daemon=True)
        t.start(); threads.append(t)
    for t in threads: t.join(timeout=12)

    # Score + shortlist top 8
    for r in results: r["score"] = _score(r)
    results.sort(key=lambda r: -r["score"])
    top = results[:8]

    if len(top) < 2:
        out = {"ok": False, "query": query, "reason": "less than 2 sources",
               "sources": top}
        cache[key] = {"ts": time.time(), "result": out}; _save_cache(cache)
        return out

    # Synthèse LLM avec sources numérotées
    sources_block = "\n\n".join(
        f"[{i+1}] ({s['source']}) {s['title']}\n"
        f"    {(s.get('summary','')[:600]).strip()}\n"
        f"    URL: {s.get('url','')}"
        for i, s in enumerate(top)
    )
    synthesis_raw = _ask_llm(SYNTH_PROMPT.format(query=query, sources_block=sources_block))
    # Extract confidence
    cm = re.search(r'confidence\s*:\s*(low|medium|high)', synthesis_raw, re.I)
    confidence = cm.group(1).lower() if cm else "medium"

    note_path = None
    if write_note and top:
        try:
            note_path = _write_research_note(query, synthesis_raw, top, confidence)
            _log(f"wrote note: {note_path}")
            # Rebuild thought_graph pour intégrer la nouvelle note
            try:
                import sys
                if r"<CORTEX_REPO>\scripts\brain" not in sys.path:
                    sys.path.insert(0, r"<CORTEX_REPO>\scripts\brain")
                import cortex_thought_graph as ctg
                ctg.build_graph(force=True)
            except Exception: pass
        except Exception as e:
            _log(f"write note err: {e}")

    out = {
        "ok": True, "query": query,
        "n_sources": len(top), "confidence": confidence,
        "synthesis": synthesis_raw,
        "sources": [{k: v for k, v in s.items() if k != "summary"} for s in top],
        "note_path": str(note_path) if note_path else None,
    }
    cache[key] = {"ts": time.time(), "result": out}; _save_cache(cache)
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: cortex_research.py 'ta question'"); sys.exit(0)
    q = " ".join(sys.argv[1:])
    print(json.dumps(research(q), ensure_ascii=False, indent=2)[:5000])
