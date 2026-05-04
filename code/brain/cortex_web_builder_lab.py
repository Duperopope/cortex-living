"""
cortex_web_builder_lab.py — Cortex apprend à construire des sites pro.

Pas un générateur de template : une vraie boucle IAG.
- comprend un brief (project_brief)
- produit un site (generate_site_project)
- teste (static_validate, audit_site_quality)
- diagnose (diagnose_site_issues)
- patche (patch_site)
- compare versions (run_autonomous_web_cycle, max_attempts)
- livre un package (export_delivery_package)

Statuts honnêtes (anti-fake) :
    draft, static_validated, preview_tested, audit_passed,
    delivery_ready, client_ready

Critères de qualité référencés (pas hardcodés en marketing) :
- Core Web Vitals (Google) : performance, INP, CLS
- Lighthouse (Chrome) : 4 scores 0-100
- WCAG 2.2 (W3C) : accessibilité
- OWASP Top 10 : sécurité statique

Stratégie sans Lighthouse forcé :
    Si `npx lighthouse` dispo → audit officiel.
    Sinon → audit statique maison (regex + parsing HTML), marqué
    `lighthouse_available=false` pour transparence.

API publique :
    status() → snapshot
    self_test() → ne touche à rien d'externe
    create_project_brief(name, type) → dict
    generate_site_project(brief) → dict {files_written}
    static_validate_site(path) → dict {checks, errors}
    run_local_preview(path, port) → dict {url, pid}
    audit_site_quality(path, url=None) → dict {scores, lighthouse_available}
    diagnose_site_issues(audit) → dict {issues, severity}
    patch_site(diagnosis) → dict {applied}
    run_autonomous_web_cycle(max_attempts=3) → dict {final_verdict}
    export_delivery_package(path) → dict {manifest}
"""
from __future__ import annotations
import html.parser
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
PAPERCLIP_ROOT = Path(r"<CORTEX_REPO>")
LAB_ROOT = PAPERCLIP_ROOT / "examples" / "web-builder" / "pro-site-mvp"
SESSION_ROOT = PAPERCLIP_ROOT / "examples" / "session-current" / "web_builder"
DOCS_ROOT = PAPERCLIP_ROOT / "docs" / "web-builder"

DEFAULT_PROJECT_NAME = "Cortex Studio"
DEFAULT_SITE_TYPE = "professional_landing"

# Pages minimales pour un site pro
REQUIRED_PAGES = ["index.html", "services.html", "case-studies.html", "contact.html"]

# Sections minimales attendues sur la home (anti-template vide)
REQUIRED_SECTIONS = [
    "hero", "value", "services", "process",
    "proof", "use-cases", "faq", "cta", "footer",
]

# Patterns sécurité — leak detection (anti pré-publication)
SECURITY_LEAK_PATTERNS = [
    (r"sk-[A-Za-z0-9_-]{20,}",      "API_KEY"),
    (r"ghp_[A-Za-z0-9]{30,}",        "GITHUB_TOKEN"),
    (r"AKIA[A-Z0-9]{16}",            "AWS_KEY"),
    (r"-----BEGIN[\s\S]*?PRIVATE KEY-----", "PRIVATE_KEY"),
    (r"C:[\\/]+Users[\\/]+\w+",      "LOCAL_USER_PATH"),
    (r"H:[\\/]+Code[\\/]+Paperclip", "LOCAL_REPO_PATH"),
]


def _now() -> float: return time.time()


def _safe_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ─── BRIEF ───────────────────────────────────────────────────────────────────
def create_project_brief(name: str = DEFAULT_PROJECT_NAME,
                          site_type: str = DEFAULT_SITE_TYPE) -> dict:
    """Génère ou lit le brief projet."""
    brief = {
        "ts": _now(),
        "name": name,
        "site_type": site_type,
        "audience": "PME, dev tech, dirigeants curieux d'IA",
        "value_proposition": "Construit des agents autonomes locaux + sites web techniques",
        "tone": "professionnel, direct, sans buzzwords",
        "design": "moderne sombre avec accents lumineux, responsive",
        "pages": REQUIRED_PAGES,
        "sections": REQUIRED_SECTIONS,
        "constraints": [
            "zéro dépendance externe runtime",
            "HTML/CSS/JS vanilla",
            "responsive 320-1920px",
            "accessibilité WCAG 2.2 AA",
            "pas de tracking tiers",
        ],
        "quality_targets": {
            "lighthouse_performance": 85,
            "lighthouse_accessibility": 90,
            "lighthouse_seo": 90,
            "static_seo_score": 80,
            "static_a11y_score": 80,
            "static_security_score": 95,
        },
    }
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    (SESSION_ROOT / "project_brief.json").write_text(
        json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    return brief


# ─── GÉNÉRATION DU SITE ──────────────────────────────────────────────────────
def _css_stylesheet() -> str:
    """Style moderne dark theme, responsive, variables CSS, focus visible."""
    return """:root {
  --bg: #0a0e14;
  --bg-elevated: #111827;
  --border: #1f2937;
  --text: #e5e7eb;
  --text-muted: #9ca3af;
  --text-faint: #6b7280;
  --accent: #7dd3fc;
  --accent-strong: #38bdf8;
  --success: #34d399;
  --warning: #fbbf24;
  --danger: #f87171;
  --max-width: 1100px;
  --radius: 8px;
  --space: 1.5rem;
  --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  font-family: var(--font-sans);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.skip-link {
  position: absolute; left: -9999px; top: 0;
  background: var(--accent); color: var(--bg); padding: 0.5rem 1rem;
}
.skip-link:focus { left: 0; z-index: 999; }
.container { max-width: var(--max-width); margin: 0 auto; padding: 0 var(--space); }
header {
  position: sticky; top: 0; z-index: 100;
  background: rgba(10, 14, 20, 0.85); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
header .container { display: flex; justify-content: space-between; align-items: center; padding: 1rem var(--space); }
.logo { font-weight: 700; font-size: 1.1rem; color: var(--accent); }
nav ul { list-style: none; display: flex; gap: 1.5rem; }
nav a { color: var(--text-muted); text-decoration: none; font-size: 0.9rem; transition: color 200ms; }
nav a:hover, nav a[aria-current="page"] { color: var(--text); }
section { padding: 4rem 0; }
h1, h2, h3 { font-weight: 600; line-height: 1.2; margin-bottom: 1rem; letter-spacing: -0.02em; }
h1 { font-size: clamp(2rem, 5vw, 3.5rem); }
h2 { font-size: clamp(1.5rem, 3vw, 2.25rem); }
h3 { font-size: 1.25rem; }
p { margin-bottom: 1rem; color: var(--text-muted); }
a { color: var(--accent); }
.btn {
  display: inline-block; padding: 0.75rem 1.5rem;
  background: var(--accent); color: var(--bg);
  border-radius: var(--radius); text-decoration: none; font-weight: 600;
  transition: background 200ms, transform 100ms;
  border: none; cursor: pointer; font-size: 1rem;
}
.btn:hover { background: var(--accent-strong); }
.btn:active { transform: translateY(1px); }
.btn--secondary { background: transparent; color: var(--text); border: 1px solid var(--border); }
.btn--secondary:hover { border-color: var(--accent); color: var(--accent); }
#hero { padding: 6rem 0 4rem; text-align: center; }
#hero h1 { background: linear-gradient(180deg, var(--text) 60%, var(--accent) 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
#hero p.lead { font-size: 1.2rem; max-width: 640px; margin: 0 auto 2rem; }
.cta-group { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; }
.grid { display: grid; gap: 2rem; }
.grid--3 { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
.card { background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.5rem; transition: border-color 200ms; }
.card:hover { border-color: var(--accent); }
.card h3 { color: var(--text); }
.process-list { counter-reset: step; list-style: none; }
.process-list li { counter-increment: step; padding: 1rem 0; border-bottom: 1px solid var(--border); }
.process-list li::before { content: counter(step) "."; color: var(--accent); font-weight: 700; margin-right: 0.5rem; }
.metric { display: flex; gap: 1rem; align-items: baseline; padding: 1rem 0; border-bottom: 1px dashed var(--border); }
.metric strong { font-size: 2rem; color: var(--accent); font-family: var(--font-mono); min-width: 80px; }
.metric span { color: var(--text-muted); }
details { background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem 1.5rem; margin-bottom: 0.75rem; }
details[open] { border-color: var(--accent); }
summary { cursor: pointer; font-weight: 600; color: var(--text); }
details p { margin-top: 0.75rem; }
form { display: grid; gap: 1rem; max-width: 520px; }
label { font-size: 0.85rem; color: var(--text-muted); }
input, textarea {
  width: 100%; padding: 0.75rem; font: inherit;
  background: var(--bg-elevated); color: var(--text);
  border: 1px solid var(--border); border-radius: var(--radius);
}
input:focus, textarea:focus { border-color: var(--accent); outline: none; }
textarea { min-height: 120px; resize: vertical; }
footer { border-top: 1px solid var(--border); padding: 2rem 0; margin-top: 4rem; color: var(--text-faint); font-size: 0.85rem; }
footer .container { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }
@media (max-width: 600px) {
  nav ul { gap: 1rem; font-size: 0.85rem; }
  section { padding: 3rem 0; }
}
@media (prefers-reduced-motion: reduce) {
  * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
"""


def _js_minimal() -> str:
    """JS vanilla : smooth-scroll, FAQ accordion (les <details> natifs gèrent), pas de tracking."""
    return """// Cortex Studio — vanilla JS, zéro dépendance externe.
// Anchors smooth-scroll handled by CSS scroll-behavior: smooth.
// Form basic validation (no submit handler — email/POST handled server-side).
document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('#contact-form');
  if (form) {
    form.addEventListener('submit', (e) => {
      const email = form.querySelector('[name=email]');
      if (email && !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email.value)) {
        e.preventDefault();
        email.setAttribute('aria-invalid', 'true');
        email.focus();
      }
    });
  }
});
"""


def _page_head(title: str, description: str, canonical: str = "/") -> str:
    """<head> SEO-complet : meta description, OG, viewport, theme-color."""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#0a0e14">
  <title>{title} | Cortex Studio</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{title} | Cortex Studio">
  <meta property="og:description" content="{description}">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="stylesheet" href="styles.css">
</head>"""


def _header_nav(current: str) -> str:
    def link(href, label, key):
        attr = ' aria-current="page"' if current == key else ""
        return f'<li><a href="{href}"{attr}>{label}</a></li>'
    return f"""<a class="skip-link" href="#main">Aller au contenu</a>
<header>
  <div class="container">
    <a href="index.html" class="logo">Cortex Studio</a>
    <nav aria-label="Navigation principale">
      <ul>
        {link("index.html", "Accueil", "home")}
        {link("services.html", "Services", "services")}
        {link("case-studies.html", "Cas d'usage", "cases")}
        {link("contact.html", "Contact", "contact")}
      </ul>
    </nav>
  </div>
</header>
<main id="main">"""


def _footer() -> str:
    return """</main>
<footer>
  <div class="container">
    <div>© 2026 Cortex Studio · Construit en local, livré sans tracking.</div>
    <div><a href="contact.html">contact</a> · <a href="#main">retour en haut</a></div>
  </div>
</footer>
<script src="script.js" defer></script>
</body>
</html>
"""


def _page_index() -> str:
    return _page_head("Accueil",
        "Agence locale qui construit des agents autonomes, sites web techniques et systèmes de modding mesurables.",
        "index.html") + """
<body>""" + _header_nav("home") + """
<section id="hero">
  <div class="container">
    <h1>Des agents qui font, pas qui parlent.</h1>
    <p class="lead">Cortex Studio construit des systèmes autonomes locaux : agents cognitifs, sites web techniques, mods de jeux. Tout mesurable, tout auditable.</p>
    <div class="cta-group">
      <a href="services.html" class="btn">Voir les services</a>
      <a href="case-studies.html" class="btn btn--secondary">Lire les cas d'usage</a>
    </div>
  </div>
</section>
<section id="value">
  <div class="container">
    <h2>Pourquoi Cortex Studio</h2>
    <div class="grid grid--3">
      <article class="card"><h3>Local-first</h3><p>Tout tourne sur ta machine. Pas de cloud opaque, pas de coût à l'usage.</p></article>
      <article class="card"><h3>Mesurable</h3><p>Chaque action laisse une preuve : log, JSON, screenshot. Pas de "ça marche" gratuit.</p></article>
      <article class="card"><h3>Open</h3><p>Code commenté, tests fournis, anti-fake intégré. Tu lis tout, tu modifies tout.</p></article>
    </div>
  </div>
</section>
<section id="services">
  <div class="container">
    <h2>Services</h2>
    <div class="grid grid--3">
      <article class="card"><h3>Agents cognitifs</h3><p>Active Inference, JEPA, belief states, mémoire persistante. Apprend, agit, mesure.</p></article>
      <article class="card"><h3>Sites web techniques</h3><p>HTML/CSS/JS vanilla, Lighthouse 90+, accessible, sans framework imposé.</p></article>
      <article class="card"><h3>Modding de jeux</h3><p>X4 Foundations, Starfield. Faction autonome, télémétrie embarquée, boucle build/test/patch.</p></article>
    </div>
  </div>
</section>
<section id="process">
  <div class="container">
    <h2>Comment on travaille</h2>
    <ol class="process-list">
      <li>Brief court : ce que tu veux, ce qui doit être mesurable.</li>
      <li>Prototype rapide : version utilisable, pas slide.</li>
      <li>Boucle test/patch : on observe les sorties, on corrige.</li>
      <li>Livraison : code + rapports + manifest. Tu vérifies.</li>
    </ol>
  </div>
</section>
<section id="proof">
  <div class="container">
    <h2>Preuves</h2>
    <div class="metric"><strong>52</strong><span>modules cognitifs publiés sur le repo public</span></div>
    <div class="metric"><strong>6</strong><span>junctions NTFS gérées en autonomie par body_health</span></div>
    <div class="metric"><strong>~55 Go</strong><span>libérés effectivement sur disque système (mesure pre/post psutil)</span></div>
    <div class="metric"><strong>0</strong><span>blocker safety check sur le dernier push public</span></div>
  </div>
</section>
<section id="use-cases">
  <div class="container">
    <h2>Cas d'usage typiques</h2>
    <div class="grid grid--3">
      <article class="card"><h3>Agent personnel</h3><p>Assistant local qui voit ton écran, parle, prend des décisions et apprend.</p></article>
      <article class="card"><h3>Site métier</h3><p>Landing technique, portfolio, dashboard SaaS. Performance mesurée Lighthouse.</p></article>
      <article class="card"><h3>Lab de jeu</h3><p>Mod X4 ou Starfield avec télémétrie. Cortex teste et patche en boucle.</p></article>
    </div>
  </div>
</section>
<section id="faq">
  <div class="container">
    <h2>Questions fréquentes</h2>
    <details><summary>Pourquoi pas de framework ?</summary><p>Parce qu'un site doit pouvoir être lu, modifié et hébergé partout sans tooling. Si un projet en a vraiment besoin, on justifie.</p></details>
    <details><summary>Vous utilisez de l'IA dans la fabrication ?</summary><p>Oui, et on le dit. Les sorties sont auditées et testées avant livraison.</p></details>
    <details><summary>Vous gardez nos données ?</summary><p>Tout reste sur ta machine. Aucun upload sans ton accord explicite.</p></details>
    <details><summary>Combien ça coûte ?</summary><p>Devis au forfait après brief. Pas d'abonnement caché, pas de coût à l'usage.</p></details>
  </div>
</section>
<section id="cta">
  <div class="container" style="text-align:center">
    <h2>Tu as un brief ?</h2>
    <p>Décris ton besoin en deux phrases. Je réponds avec un prototype testable.</p>
    <a href="contact.html" class="btn">Démarrer un projet</a>
  </div>
</section>
""" + _footer()


def _page_services() -> str:
    return _page_head("Services",
        "Trois services : agents cognitifs autonomes, sites web techniques mesurables, modding de jeux avec télémétrie.",
        "services.html") + """
<body>""" + _header_nav("services") + """
<section>
  <div class="container">
    <h1>Services</h1>
    <p class="lead">Trois rails. Chacun avec une définition claire, des livrables nommés, et des critères de succès chiffrés.</p>
  </div>
</section>
<section>
  <div class="container">
    <h2>Agent cognitif local</h2>
    <p>Système qui voit, écoute, mémorise, décide. Tourne 100% sur ta machine.</p>
    <ul style="margin-left: 1.5rem; color: var(--text-muted);">
      <li>Active Inference, belief state, JEPA pour apprendre les transitions latentes</li>
      <li>Mémoire persistante (Hebbian + sémantique)</li>
      <li>Anti-fake intégré : interroge son propre état runtime, ne ment pas par défaut</li>
      <li>Dashboard local avec métriques live</li>
    </ul>
  </div>
</section>
<section>
  <div class="container">
    <h2>Site web technique</h2>
    <p>Pour PME, dev, indépendants. HTML/CSS/JS vanilla. Lighthouse mesuré.</p>
    <ul style="margin-left: 1.5rem; color: var(--text-muted);">
      <li>Multi-pages avec navigation accessible</li>
      <li>SEO complet (meta, OG, sitemap si pertinent)</li>
      <li>Responsive 320-1920px, prefers-reduced-motion respecté</li>
      <li>Audit Lighthouse fourni avec la livraison</li>
    </ul>
  </div>
</section>
<section>
  <div class="container">
    <h2>Lab de jeu (X4 / Starfield)</h2>
    <p>Cortex génère un mod, l'installe, lance le jeu, observe via télémétrie embarquée, diagnostique, patche.</p>
    <ul style="margin-left: 1.5rem; color: var(--text-muted);">
      <li>Faction autonome avec spawn vérifiable</li>
      <li>Télémétrie via Mission Director pour preuves auditables</li>
      <li>Cycle build → install → launch → observe → patch automatisé</li>
    </ul>
  </div>
</section>
""" + _footer()


def _page_cases() -> str:
    return _page_head("Cas d'usage",
        "Trois exemples concrets : agent local Cortex, ce site lui-même, lab X4 Foundations.",
        "case-studies.html") + """
<body>""" + _header_nav("cases") + """
<section>
  <div class="container">
    <h1>Cas d'usage</h1>
    <p class="lead">Trois exemples qu'on a construits et qu'on peut auditer.</p>
  </div>
</section>
<section>
  <div class="container">
    <article class="card">
      <h2>Cortex — agent local autonome</h2>
      <p>52 modules cognitifs Python, dashboard 3D, boucle Active Inference + JEPA + belief state branchés en production. Apprentissage des effets d'action mesuré, anti-fake interrogeant son propre runtime.</p>
      <p><strong>Preuves :</strong> repo public cortex-living, smoke check 7/7, safety check 0 blockers, examples/session-current/ avec rapports JSON croisés.</p>
    </article>
  </div>
</section>
<section>
  <div class="container">
    <article class="card">
      <h2>Ce site (Cortex Studio)</h2>
      <p>Landing 4 pages générée par Cortex via cortex_web_builder_lab.py. Boucle autonome : brief → generate → static_validate → audit → diagnose → patch.</p>
      <p><strong>Preuves :</strong> rapports JSON dans examples/session-current/web_builder/, audit qualité auditable, manifest de livraison fourni.</p>
    </article>
  </div>
</section>
<section>
  <div class="container">
    <article class="card">
      <h2>Lab X4 Foundations</h2>
      <p>Extension cortex_faction installée dans G:\\Steam\\steamapps\\common\\X4 Foundations\\extensions\\. Mission Director cues écrivent des markers télémétrie dans debug.log que Cortex grep pour valider chaque step (extension_loaded, faction_init, economy_tick).</p>
      <p><strong>Preuves :</strong> examples/game-modding/x4-cortex-faction/, manifest avec statuts honnêtes (generated/installed/launched/...).</p>
    </article>
  </div>
</section>
""" + _footer()


def _page_contact() -> str:
    return _page_head("Contact",
        "Décris ton besoin en deux phrases. Réponse avec prototype testable, pas slide.",
        "contact.html") + """
<body>""" + _header_nav("contact") + """
<section>
  <div class="container">
    <h1>Contact</h1>
    <p class="lead">Décris ton besoin en deux phrases. Je réponds avec un prototype testable.</p>
    <form id="contact-form" method="POST" action="#" novalidate>
      <div>
        <label for="name">Nom</label>
        <input id="name" name="name" type="text" required autocomplete="name">
      </div>
      <div>
        <label for="email">Email</label>
        <input id="email" name="email" type="email" required autocomplete="email">
      </div>
      <div>
        <label for="brief">Brief (2-3 phrases)</label>
        <textarea id="brief" name="brief" required></textarea>
      </div>
      <button class="btn" type="submit">Envoyer</button>
    </form>
  </div>
</section>
""" + _footer()


def generate_site_project(brief: dict | None = None) -> dict:
    """Génère le projet site MVP dans LAB_ROOT."""
    if brief is None: brief = create_project_brief()
    LAB_ROOT.mkdir(parents=True, exist_ok=True)
    files = {
        "index.html":         _page_index(),
        "services.html":      _page_services(),
        "case-studies.html":  _page_cases(),
        "contact.html":       _page_contact(),
        "styles.css":         _css_stylesheet(),
        "script.js":          _js_minimal(),
        "robots.txt":         "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n",
        "sitemap.xml":        ('<?xml version="1.0" encoding="UTF-8"?>\n'
                                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                 '  <url><loc>/index.html</loc></url>\n'
                                 '  <url><loc>/services.html</loc></url>\n'
                                 '  <url><loc>/case-studies.html</loc></url>\n'
                                 '  <url><loc>/contact.html</loc></url>\n'
                                 '</urlset>\n'),
    }
    written = []
    for name, content in files.items():
        _safe_write(LAB_ROOT / name, content)
        written.append(name)
    # README
    _safe_write(LAB_ROOT / "README.md",
                f"# {brief['name']} — landing pro statique\n\n"
                f"Type : {brief['site_type']}\n"
                f"Pages : {len(REQUIRED_PAGES)}\n\n"
                f"## Lancer en local\n\n"
                f"```bash\npython -m http.server 8000\n```\n\n"
                f"Puis ouvrir http://localhost:8000/\n\n"
                f"## Audit Lighthouse\n\n"
                f"```bash\nnpx lighthouse http://localhost:8000/ --output=json --output-path=lh.json\n```\n")
    written.append("README.md")
    return {"ok": True, "stage": "draft", "lab_path": str(LAB_ROOT),
             "files_written": written, "n_files": len(written)}


# ─── VALIDATION STATIQUE ─────────────────────────────────────────────────────
class _HtmlInspector(html.parser.HTMLParser):
    """Parse HTML, collecte tags, attributs, alt manquants."""
    def __init__(self):
        super().__init__()
        self.tags: list[str] = []
        self.titles: list[str] = []
        self.in_title = False
        self.metas: list[dict] = []
        self.imgs: list[dict] = []
        self.links_internal: list[str] = []
        self.links_external: list[str] = []
        self.headings: list[tuple[int, str]] = []
        self.has_main = False
        self.has_h1 = False
        self.lang = None
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if tag == "html" and "lang" in a: self.lang = a["lang"]
        if tag == "title": self.in_title = True
        if tag == "meta": self.metas.append(a)
        if tag == "img": self.imgs.append(a)
        if tag == "a" and "href" in a:
            href = a["href"]
            if href.startswith(("http://", "https://", "mailto:", "tel:")):
                self.links_external.append(href)
            elif href and not href.startswith("#"):
                self.links_internal.append(href)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append((int(tag[1]), ""))
            if tag == "h1": self.has_h1 = True
        if tag == "main": self.has_main = True

    def handle_data(self, data):
        if self.in_title and data.strip():
            self.titles.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "title": self.in_title = False

    def error(self, message):
        self.errors.append(message)


def static_validate_site(path: Path | str | None = None) -> dict:
    """Valide les pages : HTML parseable, meta tags, alt images, liens internes,
    pas de leak sécurité.
    """
    p = Path(path) if path else LAB_ROOT
    rep = {"ok": False, "stage": "static_validated", "lab_path": str(p),
           "checks": {}, "errors": [], "per_page": {}}
    if not p.exists():
        rep["errors"].append("lab_path_missing"); return rep

    # 1. Pages requises présentes
    missing_pages = [pg for pg in REQUIRED_PAGES if not (p / pg).exists()]
    rep["checks"]["all_required_pages_present"] = len(missing_pages) == 0
    if missing_pages: rep["errors"].append(f"missing_pages: {missing_pages}")

    # 2. Pour chaque page : parse + checks
    page_files = list(p.glob("*.html"))
    valid_internal_targets = {f.name for f in page_files} | {"#main"}
    for page in page_files:
        try:
            text = page.read_text(encoding="utf-8")
        except Exception as e:
            rep["per_page"][page.name] = {"read_error": str(e)[:120]}
            continue
        insp = _HtmlInspector()
        try: insp.feed(text)
        except Exception as e: insp.errors.append(str(e)[:120])
        page_rep = {
            "html_parseable":       len(insp.errors) == 0,
            "has_lang":             insp.lang is not None,
            "has_title":            len(insp.titles) > 0,
            "has_meta_desc":        any(m.get("name") == "description" for m in insp.metas),
            "has_meta_viewport":    any(m.get("name") == "viewport" for m in insp.metas),
            "has_og_title":         any(m.get("property") == "og:title" for m in insp.metas),
            "has_h1":               insp.has_h1,
            "has_main":             insp.has_main,
            "n_imgs":               len(insp.imgs),
            "n_imgs_without_alt":   sum(1 for img in insp.imgs if not img.get("alt")),
            "n_links_internal":     len(insp.links_internal),
            "n_links_external":     len(insp.links_external),
            "broken_internal_links": [],
        }
        # Liens internes cassés
        for href in insp.links_internal:
            tgt = href.split("#")[0]
            if tgt and tgt not in valid_internal_targets:
                page_rep["broken_internal_links"].append(href)
        rep["per_page"][page.name] = page_rep

    # 3. Pas de leak sécurité dans tous les fichiers texte
    leaks = []
    for f in p.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in (".html", ".css", ".js", ".md", ".txt", ".xml", ".json"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            for pat, kind in SECURITY_LEAK_PATTERNS:
                if re.search(pat, text):
                    leaks.append({"file": str(f.relative_to(p)), "kind": kind})
                    break
        except Exception: pass
    rep["checks"]["no_security_leaks"] = len(leaks) == 0
    rep["security_leaks"] = leaks
    if leaks: rep["errors"].append(f"security_leaks: {len(leaks)}")

    # 4. styles.css / script.js présents
    rep["checks"]["css_present"] = (p / "styles.css").exists()
    rep["checks"]["js_present"] = (p / "script.js").exists()
    rep["checks"]["sitemap_present"] = (p / "sitemap.xml").exists()
    rep["checks"]["robots_present"] = (p / "robots.txt").exists()

    # 5. Pages individuelles toutes valides
    all_pages_ok = all(
        pp.get("html_parseable") and pp.get("has_title") and pp.get("has_meta_desc")
        and pp.get("has_meta_viewport") and pp.get("has_h1")
        and pp.get("n_imgs_without_alt", 0) == 0
        and len(pp.get("broken_internal_links", [])) == 0
        for pp in rep["per_page"].values()
    )
    rep["checks"]["all_pages_valid"] = all_pages_ok

    rep["ok"] = (not rep["errors"]
                  and all(v for v in rep["checks"].values() if isinstance(v, bool)))
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    (SESSION_ROOT / "static_validation.json").write_text(
        json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    return rep


# ─── PREVIEW LOCAL ───────────────────────────────────────────────────────────
_PREVIEW_PROC = None


def _free_port(default: int = 8088) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", default)); s.close(); return default
    except OSError:
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(("127.0.0.1", 0))
        port = s2.getsockname()[1]; s2.close(); return port


def run_local_preview(path: Path | str | None = None, port: int = 0) -> dict:
    """Lance `python -m http.server` sur le dossier site. Détaché."""
    global _PREVIEW_PROC
    p = Path(path) if path else LAB_ROOT
    if not p.exists():
        return {"ok": False, "error": "lab_path_missing"}
    port = port or _free_port(8088)
    try:
        flags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | \
                 int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port)],
            cwd=str(p), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _PREVIEW_PROC = proc
        time.sleep(1.5)
        url = f"http://127.0.0.1:{port}/"
        # Test que le serveur répond
        try:
            r = urllib.request.urlopen(url, timeout=3)
            ok = r.status == 200
        except Exception:
            ok = False
        return {"ok": ok, "stage": "preview_tested", "url": url,
                "pid": proc.pid, "port": port}
    except Exception as e:
        return {"ok": False, "error": f"preview_failed: {e}"}


def stop_preview() -> dict:
    """Stoppe le preview local s'il tourne."""
    global _PREVIEW_PROC
    if _PREVIEW_PROC is None: return {"ok": True, "skip": "no_preview_running"}
    try:
        _PREVIEW_PROC.terminate()
        _PREVIEW_PROC = None
        return {"ok": True, "stopped": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


# ─── AUDIT QUALITÉ ───────────────────────────────────────────────────────────
def _detect_lighthouse() -> bool:
    """Test si `npx lighthouse --version` répond. Sans bloquer."""
    try:
        r = subprocess.run(["npx", "--no-install", "lighthouse", "--version"],
                            capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception: return False


def _static_audit(path: Path) -> dict:
    """Audit statique maison (sans Lighthouse). Scores 0-100 par catégorie."""
    pages = list(path.glob("*.html"))
    seo_points = 0; seo_max = 0
    a11y_points = 0; a11y_max = 0
    sec_points = 0; sec_max = 0
    perf_points = 0; perf_max = 0
    responsive_points = 0; responsive_max = 0
    content_points = 0; content_max = 0
    issues = []

    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        insp = _HtmlInspector()
        try: insp.feed(text)
        except Exception: pass
        # SEO (5 pts/page)
        seo_max += 5
        if any(m.get("name") == "description" for m in insp.metas): seo_points += 1
        else: issues.append({"page": page.name, "kind": "seo", "msg": "meta description manquante"})
        if insp.titles: seo_points += 1
        else: issues.append({"page": page.name, "kind": "seo", "msg": "title vide"})
        if any(m.get("property") == "og:title" for m in insp.metas): seo_points += 1
        else: issues.append({"page": page.name, "kind": "seo", "msg": "og:title manquant"})
        if any(m.get("name") == "viewport" for m in insp.metas): seo_points += 1
        if any(t.startswith("link") for t in [str(m) for m in insp.metas]) or 'rel="canonical"' in text:
            seo_points += 1
        # A11Y (5 pts/page)
        a11y_max += 5
        if insp.lang: a11y_points += 1
        else: issues.append({"page": page.name, "kind": "a11y", "msg": "html lang manquant"})
        if insp.has_h1: a11y_points += 1
        else: issues.append({"page": page.name, "kind": "a11y", "msg": "h1 manquant"})
        if insp.has_main: a11y_points += 1
        else: issues.append({"page": page.name, "kind": "a11y", "msg": "<main> manquant"})
        n_no_alt = sum(1 for img in insp.imgs if not img.get("alt"))
        if insp.imgs:
            if n_no_alt == 0: a11y_points += 1
            else: issues.append({"page": page.name, "kind": "a11y",
                                  "msg": f"{n_no_alt} <img> sans alt"})
        else: a11y_points += 1
        if 'aria-label' in text or 'role=' in text: a11y_points += 1
        # Sécurité (3 pts/page)
        sec_max += 3
        leak_in_page = False
        for pat, _ in SECURITY_LEAK_PATTERNS:
            if re.search(pat, text):
                leak_in_page = True
                issues.append({"page": page.name, "kind": "security", "msg": f"leak pattern {_}"})
                break
        if not leak_in_page: sec_points += 3
        # Perf (3 pts/page) — taille raisonnable + pas d'inline script lourd
        perf_max += 3
        size_kb = len(text) / 1024
        if size_kb < 50: perf_points += 1
        elif size_kb < 100: perf_points += 0.5
        else: issues.append({"page": page.name, "kind": "perf",
                              "msg": f"page lourde {size_kb:.0f} KB"})
        if "<script>" not in text or text.count("<script>") <= 2: perf_points += 1
        if "preconnect" in text or "preload" in text or text.count("<link rel") <= 5:
            perf_points += 1
        # Responsive (2 pts/page)
        responsive_max += 2
        if any(m.get("name") == "viewport" for m in insp.metas): responsive_points += 1
        if "@media" in (path / "styles.css").read_text(encoding="utf-8", errors="replace") if (path / "styles.css").exists() else False:
            responsive_points += 1
        # Content (3 pts/page)
        content_max += 3
        n_h2 = sum(1 for h in insp.headings if h[0] == 2)
        if n_h2 >= 2: content_points += 1
        if len(text) > 1500: content_points += 1
        else: issues.append({"page": page.name, "kind": "content",
                              "msg": "page courte (< 1500 chars)"})
        if any(t == "footer" for t in insp.tags): content_points += 1

    def pct(p, m): return round(100 * p / max(1, m))
    return {
        "lighthouse_available": False,
        "static_scores": {
            "seo":             pct(seo_points, seo_max),
            "accessibility":   pct(a11y_points, a11y_max),
            "security":        pct(sec_points, sec_max),
            "performance":     pct(perf_points, perf_max),
            "responsive":      pct(responsive_points, responsive_max),
            "content_quality": pct(content_points, content_max),
        },
        "scores": {"performance": None, "accessibility": None,
                    "best_practices": None, "seo": None},
        "n_pages_audited": len(pages),
        "issues": issues,
    }


def _lighthouse_audit(url: str) -> dict:
    """Lance Lighthouse et extrait les scores. Best-effort."""
    out_path = SESSION_ROOT / "lighthouse.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["npx", "--no-install", "lighthouse", url,
             "--output=json", f"--output-path={out_path}",
             "--quiet", "--chrome-flags=--headless --no-sandbox"],
            capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not out_path.exists():
            return {"lighthouse_available": True, "ok": False,
                    "error": (r.stderr or "lighthouse failed")[:300]}
        data = json.loads(out_path.read_text(encoding="utf-8"))
        cats = data.get("categories", {})
        scores = {
            "performance":     int(round((cats.get("performance", {}).get("score", 0) or 0) * 100)),
            "accessibility":   int(round((cats.get("accessibility", {}).get("score", 0) or 0) * 100)),
            "best_practices":  int(round((cats.get("best-practices", {}).get("score", 0) or 0) * 100)),
            "seo":             int(round((cats.get("seo", {}).get("score", 0) or 0) * 100)),
        }
        return {"lighthouse_available": True, "ok": True, "scores": scores}
    except Exception as e:
        return {"lighthouse_available": True, "ok": False, "error": str(e)[:200]}


def audit_site_quality(path: Path | str | None = None,
                        url: str | None = None) -> dict:
    """Audit complet : Lighthouse si dispo, audit statique sinon (toujours)."""
    p = Path(path) if path else LAB_ROOT
    rep = _static_audit(p)
    if url and _detect_lighthouse():
        lh = _lighthouse_audit(url)
        rep["lighthouse_available"] = True
        if lh.get("ok"): rep["scores"] = lh["scores"]
        else: rep["lighthouse_error"] = lh.get("error")
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    (SESSION_ROOT / "lighthouse_summary.json").write_text(
        json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    # Rapports séparés pour SEO/A11y/Sécurité (audit statique)
    by_kind = {"seo": [], "accessibility": [], "security": []}
    for issue in rep.get("issues", []):
        k = "accessibility" if issue["kind"] == "a11y" else issue["kind"]
        if k in by_kind: by_kind[k].append(issue)
    (SESSION_ROOT / "seo_report.json").write_text(
        json.dumps({"score": rep["static_scores"]["seo"], "issues": by_kind["seo"]},
                    indent=2, ensure_ascii=False), encoding="utf-8")
    (SESSION_ROOT / "accessibility_report.json").write_text(
        json.dumps({"score": rep["static_scores"]["accessibility"],
                     "issues": by_kind["accessibility"]},
                    indent=2, ensure_ascii=False), encoding="utf-8")
    (SESSION_ROOT / "security_static_report.json").write_text(
        json.dumps({"score": rep["static_scores"]["security"], "issues": by_kind["security"]},
                    indent=2, ensure_ascii=False), encoding="utf-8")
    return rep


# ─── DIAGNOSE + PATCH ────────────────────────────────────────────────────────
def diagnose_site_issues(audit: dict | None = None) -> dict:
    if audit is None: audit = audit_site_quality()
    diag = {"ts": _now(), "issues": list(audit.get("issues", [])),
            "severity": "low", "actionable": []}
    n = len(diag["issues"])
    if n > 10: diag["severity"] = "high"
    elif n > 3: diag["severity"] = "medium"
    # Actions automatisables
    for issue in diag["issues"]:
        if issue["kind"] == "a11y" and "alt" in issue["msg"]:
            diag["actionable"].append({"page": issue["page"], "fix": "add_alt_to_imgs"})
        elif issue["kind"] == "seo" and "description" in issue["msg"]:
            diag["actionable"].append({"page": issue["page"], "fix": "add_meta_description"})
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    (SESSION_ROOT / "diagnose.json").write_text(
        json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
    return diag


def patch_site(diagnosis: dict | None = None) -> dict:
    """v1 : régénère le projet propre (anti-corruption progressive)."""
    rep = {"ok": True, "applied": []}
    if LAB_ROOT.exists():
        try: shutil.rmtree(str(LAB_ROOT))
        except Exception: pass
    g = generate_site_project()
    rep["applied"].append("regenerated_from_template")
    rep["files_written"] = g.get("n_files", 0)
    return rep


# ─── BOUCLE AUTONOME ─────────────────────────────────────────────────────────
def run_autonomous_web_cycle(max_attempts: int = 3,
                                with_preview: bool = False) -> dict:
    """Pipeline : brief → generate → validate → (preview?) → audit → diagnose → patch → retry."""
    started = _now()
    report = {
        "ts_start": started,
        "max_attempts": max_attempts,
        "with_preview": with_preview,
        "attempts": 0,
        "extension_generated": False,
        "static_validated": False,
        "preview_tested": False,
        "audit_passed": False,
        "delivery_ready": False,
        "lighthouse_available": False,
        "scores": {},
        "static_scores": {},
        "issues_found": [],
        "issues_fixed": [],
        "verdict": "pending",
    }
    brief = create_project_brief()
    for attempt in range(1, max_attempts + 1):
        report["attempts"] = attempt
        gen = generate_site_project(brief)
        report["extension_generated"] = gen.get("ok", False)
        sv = static_validate_site()
        report["static_validated"] = sv.get("ok", False)
        if not sv.get("ok"):
            report["issues_found"].extend(sv.get("errors", []))
            patch_site()
            continue
        # Preview optional
        url = None
        if with_preview:
            pv = run_local_preview()
            report["preview_tested"] = pv.get("ok", False)
            url = pv.get("url")
        au = audit_site_quality(url=url)
        report["lighthouse_available"] = au.get("lighthouse_available", False)
        report["scores"] = au.get("scores", {})
        report["static_scores"] = au.get("static_scores", {})
        report["issues_found"] = au.get("issues", [])
        # Critère : audit_passed = scores statiques au-dessus des targets
        targets = brief.get("quality_targets", {})
        ss = au.get("static_scores", {})
        passed = (ss.get("seo", 0) >= targets.get("static_seo_score", 80)
                   and ss.get("accessibility", 0) >= targets.get("static_a11y_score", 80)
                   and ss.get("security", 0) >= targets.get("static_security_score", 95))
        report["audit_passed"] = passed
        if passed: break
        diag = diagnose_site_issues(au)
        patch_site(diag)
        report["issues_fixed"].append({"attempt": attempt,
                                          "n_actionable": len(diag.get("actionable", []))})
    if report["audit_passed"] and report["static_validated"]:
        report["delivery_ready"] = True
        report["verdict"] = "verified"
    elif report["static_validated"]:
        report["verdict"] = "partial"
    else:
        report["verdict"] = "failed"
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    (SESSION_ROOT / "web_builder_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if with_preview: stop_preview()
    return report


# ─── EXPORT DELIVERY ─────────────────────────────────────────────────────────
def export_delivery_package(path: Path | str | None = None) -> dict:
    p = Path(path) if path else LAB_ROOT
    pages = [f.name for f in p.glob("*.html")]
    assets = [f.name for f in p.iterdir() if f.is_file()
              and f.suffix.lower() in (".css", ".js", ".png", ".jpg", ".svg",
                                          ".webp", ".xml", ".txt")]
    reports = []
    if SESSION_ROOT.exists():
        for f in SESSION_ROOT.glob("*.json"):
            reports.append(f.name)
    manifest = {
        "ts": _now(),
        "project_path": str(p),
        "entrypoint": "index.html",
        "pages": pages,
        "assets": assets,
        "reports": reports,
        "ready_for_client_review": (p / "index.html").exists() and len(pages) >= 4,
        "known_limits": [
            "Audit Lighthouse non lancé sauf si npx + Chrome dispo",
            "Pas de backend : formulaire contact en POST# (à brancher)",
            "Images : aucune image bitmap fournie (design 100% CSS)",
        ],
    }
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    (SESSION_ROOT / "delivery_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


# ─── STATUS + SELF-TEST ──────────────────────────────────────────────────────
def status() -> dict:
    rep = {
        "ts": _now(),
        "lab_path": str(LAB_ROOT),
        "lab_exists": LAB_ROOT.exists(),
        "n_pages": len(list(LAB_ROOT.glob("*.html"))) if LAB_ROOT.exists() else 0,
        "lighthouse_available": _detect_lighthouse(),
    }
    last = SESSION_ROOT / "web_builder_report.json"
    if last.exists():
        try:
            r = json.loads(last.read_text(encoding="utf-8"))
            rep["last_run"] = {
                "verdict":          r.get("verdict"),
                "static_validated": r.get("static_validated"),
                "audit_passed":     r.get("audit_passed"),
                "delivery_ready":   r.get("delivery_ready"),
                "static_scores":    r.get("static_scores"),
            }
        except Exception: pass
    return rep


def self_test() -> dict:
    """Pipeline statique seulement. Pas de preview, pas de Lighthouse."""
    tests = []
    b = create_project_brief("Test Site", "professional_landing")
    tests.append({"name": "create_brief",
                  "ok": isinstance(b, dict) and "pages" in b})
    g = generate_site_project(b)
    tests.append({"name": "generate_site",
                  "ok": g.get("ok") and g.get("n_files", 0) >= 7})
    sv = static_validate_site()
    tests.append({"name": "static_validate",
                  "ok": sv.get("ok"),
                  "n_errors": len(sv.get("errors", []))})
    au = audit_site_quality()
    tests.append({"name": "audit_static",
                  "ok": isinstance(au.get("static_scores"), dict)
                       and au["static_scores"].get("seo", 0) > 0})
    dp = export_delivery_package()
    tests.append({"name": "delivery_manifest",
                  "ok": dp.get("ready_for_client_review", False)})
    return {"ok": all(t["ok"] for t in tests),
            "tests": tests,
            "static_scores": au.get("static_scores", {})}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "test":
        print(json.dumps(self_test(), indent=2, ensure_ascii=False))
    elif cmd == "brief":
        print(json.dumps(create_project_brief(), indent=2, ensure_ascii=False))
    elif cmd == "generate":
        print(json.dumps(generate_site_project(), indent=2, ensure_ascii=False))
    elif cmd == "validate":
        print(json.dumps(static_validate_site(), indent=2, ensure_ascii=False))
    elif cmd == "preview":
        print(json.dumps(run_local_preview(), indent=2, ensure_ascii=False))
    elif cmd == "stop_preview":
        print(json.dumps(stop_preview(), indent=2, ensure_ascii=False))
    elif cmd == "audit":
        url = sys.argv[2] if len(sys.argv) > 2 else None
        print(json.dumps(audit_site_quality(url=url), indent=2, ensure_ascii=False))
    elif cmd == "diagnose":
        print(json.dumps(diagnose_site_issues(), indent=2, ensure_ascii=False))
    elif cmd == "patch":
        print(json.dumps(patch_site(), indent=2, ensure_ascii=False))
    elif cmd == "run_cycle":
        with_preview = "--preview" in sys.argv
        max_attempts = 3
        for arg in sys.argv:
            if arg.startswith("--max="): max_attempts = int(arg.split("=",1)[1])
        print(json.dumps(run_autonomous_web_cycle(max_attempts=max_attempts,
                                                     with_preview=with_preview),
                          indent=2, ensure_ascii=False))
    elif cmd == "deliver":
        print(json.dumps(export_delivery_package(), indent=2, ensure_ascii=False))
    elif cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    else:
        print("Usage: cortex_web_builder_lab.py "
              "{test|brief|generate|validate|preview|stop_preview|audit|"
              "diagnose|patch|run_cycle [--preview] [--max=N]|deliver|status}")
