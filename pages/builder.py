"""GitHub Pages builder.

Generates and maintains the static site in docs/:

  docs/index.html              — list of all weekly digests, newest first
  docs/digests/YYYY-CWxx.html  — individual digest page
  docs/about.html              — static (not touched here; written once)

A manifest file (docs/manifest.json) is the source of truth for the index.
It is updated on each run and the index is regenerated from it.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / 'templates'
_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(['html']),
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _cw_label(digest: dict) -> str:
    dt = datetime.fromisoformat(digest['period_end'].replace('Z', '+00:00'))
    iso = dt.isocalendar()
    return f"{iso.year}-CW{iso.week:02d}"


def _group_by_author(articles: list[dict]) -> list[tuple[str, list[dict]]]:
    """Return [(author, [articles])] preserving first-appearance order."""
    seen: list[str] = []
    by_author: dict[str, list[dict]] = {}
    for a in articles:
        author = a['author']
        if author not in by_author:
            by_author[author] = []
            seen.append(author)
        by_author[author].append(a)
    return [(author, by_author[author]) for author in seen]


# ── Manifest ───────────────────────────────────────────────────────────────────

def _load_manifest(docs_dir: Path) -> list[dict]:
    p = docs_dir / 'manifest.json'
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_manifest(docs_dir: Path, entries: list[dict]) -> None:
    p = docs_dir / 'manifest.json'
    p.write_text(json.dumps(entries, indent=2))


# ── Digest page ────────────────────────────────────────────────────────────────

def write_digest_page(digest: dict, docs_dir: Path) -> Path:
    """Write docs/digests/YYYY-CWxx.html for the given digest."""
    cw = _cw_label(digest)
    out_path = docs_dir / 'digests' / f'{cw}.html'
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmpl = _jinja_env.get_template('digest_page.html')
    html = tmpl.render(
        cw=cw,
        period_start=digest.get('period_start', '')[:10],
        period_end=digest.get('period_end', '')[:10],
        global_summary=digest.get('global_summary', ''),
        articles=digest.get('articles', []),
        authors=_group_by_author(digest.get('articles', [])),
    )
    out_path.write_text(html, encoding='utf-8')
    logger.info("Digest page written: %s", out_path)
    return out_path


# ── Index page ─────────────────────────────────────────────────────────────────

def update_index(digest: dict, docs_dir: Path) -> None:
    """Add this digest to the manifest and regenerate docs/index.html."""
    cw = _cw_label(digest)
    period_start = digest.get('period_start', '')[:10]
    period_end = digest.get('period_end', '')[:10]
    articles = digest.get('articles', [])
    summary = digest.get('global_summary', '')
    summary_short = summary[:200] + ('…' if len(summary) > 200 else '')

    manifest = _load_manifest(docs_dir)
    manifest = [e for e in manifest if e.get('cw') != cw]
    manifest.insert(0, {
        'cw': cw,
        'period_start': period_start,
        'period_end': period_end,
        'articles_count': len(articles),
        'summary_short': summary_short,
        'page': f'digests/{cw}.html',
    })
    _save_manifest(docs_dir, manifest)
    _write_index(docs_dir, manifest)


def _write_index(docs_dir: Path, manifest: list[dict]) -> None:
    tmpl = _jinja_env.get_template('index.html')
    html = tmpl.render(entries=manifest)
    index_path = docs_dir / 'index.html'
    index_path.write_text(html, encoding='utf-8')
    logger.info("Index page written: %s", index_path)


# ── Entry point ────────────────────────────────────────────────────────────────

def update_pages(digest: dict, docs_dir: str) -> None:
    """Generate the digest page and update the index. Called from main pipeline."""
    d = Path(docs_dir)
    write_digest_page(digest, d)
    update_index(digest, d)
