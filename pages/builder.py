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

logger = logging.getLogger(__name__)

# ── Shared styles ──────────────────────────────────────────────────────────────

_CSS = """
  body {
    font-family: Georgia, serif;
    max-width: 680px;
    margin: 60px auto;
    padding: 0 20px;
    color: #222;
    line-height: 1.7;
  }
  h1 { font-size: 1.3em; font-weight: normal; letter-spacing: 0.01em; }
  h2 { font-size: 1.05em; margin-top: 2em; }
  h3 { font-size: 1em; margin-bottom: 0.2em; }
  a { color: #1a0dab; }
  nav { margin: 1.5em 0; font-size: 0.9em; }
  nav a { margin-right: 1.5em; color: #666; text-decoration: none; }
  nav a:hover { text-decoration: underline; }
  .meta { color: #888; font-size: 0.85em; }
  hr { border: none; border-top: 1px solid #eee; margin: 2em 0; }
  footer { margin-top: 4em; font-size: 0.8em; color: #aaa; border-top: 1px solid #eee; padding-top: 1.5em; line-height: 1.6; }
"""

_NAV = '<nav><a href="../index.html">Index</a><a href="../about.html">About</a><a href="../feed.xml">RSS</a></nav>'
_NAV_ROOT = '<nav><a href="about.html">About</a><a href="feed.xml">RSS</a></nav>'

_FOOTER = """
<footer>
  This digest tracks what a curated list of leading software engineers are publicly
  writing about AI &mdash; its impact on the profession, how they use it, and where
  they think it is going.<br><br>
  All content belongs to the original authors. Links to source articles are always
  included. Summaries and bios are AI-generated and may contain inaccuracies.
</footer>
"""


def _esc(text: str) -> str:
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


_ANALYTICS = """\
<!-- 100% privacy-first analytics -->
<script data-collect-dnt="true" async src="https://scripts.simpleanalyticscdn.com/latest.js"></script>
<noscript><img src="https://queue.simpleanalyticscdn.com/noscript.gif?collect-dnt=true" alt="" referrerpolicy="no-referrer-when-downgrade"/></noscript>"""


def _page(title: str, nav: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)}</title>
  <style>{_CSS}</style>
</head>
<body>
  <h1>{_esc(title)}</h1>
  {nav}
  <hr>
  {body}
  {_FOOTER}
  {_ANALYTICS}
</body>
</html>"""


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


def _cw_label(digest: dict) -> str:
    dt = datetime.fromisoformat(digest['period_end'].replace('Z', '+00:00'))
    iso = dt.isocalendar()
    return f"{iso.year}-CW{iso.week:02d}"


# ── Digest page ────────────────────────────────────────────────────────────────

def write_digest_page(digest: dict, docs_dir: Path) -> Path:
    """Write docs/digests/YYYY-CWxx.html for the given digest."""
    cw = _cw_label(digest)
    out_path = docs_dir / 'digests' / f'{cw}.html'
    out_path.parent.mkdir(parents=True, exist_ok=True)

    period_start = digest.get('period_start', '')[:10]
    period_end = digest.get('period_end', '')[:10]
    articles = digest.get('articles', [])

    # Group by author
    authors_seen: list[str] = []
    by_author: dict[str, list[dict]] = {}
    for a in articles:
        author = a['author']
        if author not in by_author:
            by_author[author] = []
            authors_seen.append(author)
        by_author[author].append(a)

    parts = [
        f'<p class="meta">{period_start} &ndash; {period_end} &middot; {len(articles)} article{"s" if len(articles) != 1 else ""}</p>',
        f'<p>{_esc(digest.get("global_summary", ""))}</p>',
        '<hr>',
    ]

    for author in authors_seen:
        author_articles = by_author[author]
        bio = author_articles[0].get('bio', '')
        parts.append(f'<h2>{_esc(author)}</h2>')
        if bio:
            parts.append(f'<p class="meta">{_esc(bio)}</p>')
        for a in author_articles:
            pub = a.get('published_at', '')[:10] if a.get('published_at') else ''
            pub_str = f' <span class="meta">({pub})</span>' if pub else ''
            parts += [
                f'<h3><a href="{_esc(a["url"])}">{_esc(a["title"])}</a>{pub_str}</h3>',
                f'<p>{_esc(a["summary"])}</p>',
            ]

    body = '\n  '.join(parts)
    out_path.write_text(_page(f'SWE AI Digest — {cw}', _NAV, body), encoding='utf-8')
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

    # Replace existing entry for same CW, or prepend
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
    if not manifest:
        entries_html = '<p class="meta">No digests yet.</p>'
    else:
        entries_html = '\n'.join(_render_index_entry(e) for e in manifest)

    body = (
        '<p>A weekly digest of what leading software engineers are writing about AI.</p>\n'
        f'{entries_html}'
    )
    index_path = docs_dir / 'index.html'
    index_path.write_text(_page('SWE AI Digest', _NAV_ROOT, body), encoding='utf-8')
    logger.info("Index page written: %s", index_path)


def _render_index_entry(entry: dict) -> str:
    count = entry['articles_count']
    article_word = 'article' if count == 1 else 'articles'
    return (
        f'<div style="margin: 1.8em 0;">\n'
        f'  <a href="{_esc(entry["page"])}" style="font-size: 1.05em;">\n'
        f'    {_esc(entry["cw"])}\n'
        f'  </a>\n'
        f'  <span class="meta">\n'
        f'    {_esc(entry["period_start"])} &ndash; {_esc(entry["period_end"])}\n'
        f'    &middot; {count} {article_word}\n'
        f'  </span>\n'
        f'  <p style="margin: 0.3em 0 0; color: #444; font-size: 0.93em;">\n'
        f'    {_esc(entry["summary_short"])}\n'
        f'  </p>\n'
        f'</div>'
    )


# ── Entry point ────────────────────────────────────────────────────────────────

def update_pages(digest: dict, docs_dir: str) -> None:
    """Generate the digest page and update the index. Called from main pipeline."""
    d = Path(docs_dir)
    write_digest_page(digest, d)
    update_index(digest, d)
