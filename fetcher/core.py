"""Core fetch logic for the SWE AI Digest.

Adapted from swe-digest/server/digest_core.py. Main change: articles now carry
a `content` field with up to max_words words of full text for AI processing,
in addition to the short `summary` excerpt.
"""

import asyncio
import calendar
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser


# ── Date helpers ───────────────────────────────────────────────────────────────

def parse_date(value) -> datetime | None:
    """Parse any date representation to a timezone-aware datetime."""
    if not value:
        return None
    if hasattr(value, 'tm_year'):
        ts = calendar.timegm(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = dateutil_parser.parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


def entry_date(entry) -> datetime | None:
    """Extract the best available date from a feedparser entry."""
    for field in ('published_parsed', 'updated_parsed', 'created_parsed'):
        d = parse_date(getattr(entry, field, None))
        if d is not None:
            return d
    for field in ('published', 'updated', 'created'):
        d = parse_date(getattr(entry, field, None))
        if d is not None:
            return d
    return None


def entry_summary(entry, max_chars: int = 500) -> str:
    """Extract a short plain-text excerpt from a feedparser entry."""
    raw = ''
    if hasattr(entry, 'content') and entry.content:
        raw = entry.content[0].get('value', '')
    elif hasattr(entry, 'summary') and entry.summary:
        raw = entry.summary
    if not raw:
        return ''
    text = BeautifulSoup(raw, 'html.parser').get_text(separator=' ', strip=True)
    return text[:max_chars]


def entry_content_for_ai(entry, max_words: int = 2000) -> str:
    """Extract full plain-text content for AI processing, up to max_words words.

    Prefers entry.content (full text) over entry.summary (excerpt). HTML is
    stripped. Content is word-truncated, not character-truncated, so the AI
    always receives complete sentences up to the limit.
    """
    raw = ''
    if hasattr(entry, 'content') and entry.content:
        raw = entry.content[0].get('value', '')
    elif hasattr(entry, 'summary') and entry.summary:
        raw = entry.summary
    if not raw:
        return ''
    text = BeautifulSoup(raw, 'html.parser').get_text(separator=' ', strip=True)
    words = text.split()
    if len(words) > max_words:
        return ' '.join(words[:max_words]) + ' [truncated]'
    return ' '.join(words)


# ── HTML scraper registry ──────────────────────────────────────────────────────

_SCRAPERS: dict = {}


def scraper(url_prefix: str):
    """Decorator to register a site-specific scraper."""
    def decorator(fn):
        _SCRAPERS[url_prefix] = fn
        return fn
    return decorator


@scraper('https://norvig.com')
def scrape_norvig(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Norvig's personal site — scrape essay/article links."""
    results = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        if len(text) < 15 or len(text) > 200:
            continue
        parsed = urlparse(href)
        if parsed.scheme in ('http', 'https') and 'norvig.com' not in parsed.netloc:
            continue
        full_url = urljoin(base_url, href)
        results.append({'title': text, 'url': full_url, 'published': None,
                        'summary': '', 'content': ''})
    return results[:30]


def scrape_generic(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Generic scraper — find article/post links on a page."""
    results = []
    seen_urls: set[str] = set()
    containers = soup.find_all(['article', 'main', 'section', 'div'], limit=5)
    link_pool = []
    for container in containers:
        link_pool.extend(container.find_all('a', href=True))
    if not link_pool:
        link_pool = soup.find_all('a', href=True)
    for a in link_pool:
        href = a['href']
        text = a.get_text(strip=True)
        if len(text) < 15 or len(text) > 200:
            continue
        full_url = urljoin(base_url, href)
        if full_url in seen_urls:
            continue
        parsed = urlparse(full_url)
        if not parsed.scheme.startswith('http'):
            continue
        seen_urls.add(full_url)
        results.append({'title': text, 'url': full_url, 'published': None,
                        'summary': '', 'content': ''})
    return results[:20]


# ── RSS/Atom fetcher ───────────────────────────────────────────────────────────

async def fetch_rss(
    client: httpx.AsyncClient,
    engineer: dict,
    source: dict,
    cutoff: datetime,
    max_content_words: int = 2000,
) -> tuple[list[dict], str | None]:
    """Fetch one RSS/Atom feed and return articles newer than cutoff."""
    articles = []
    url = source['url']
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            return [], f"Invalid feed at {url}: {feed.bozo_exception}"
        for entry in feed.entries:
            pub = entry_date(entry)
            if pub and pub < cutoff:
                continue
            articles.append({
                'engineer': engineer['name'],
                'priority': engineer.get('priority', 99),
                'bio': engineer.get('bio', ''),
                'source_type': 'rss',
                'source_label': source.get('label', ''),
                'source_url': url,
                'title': getattr(entry, 'title', '(no title)').strip(),
                'url': getattr(entry, 'link', ''),
                'published': pub.isoformat() if pub else None,
                'summary': entry_summary(entry),
                'content': entry_content_for_ai(entry, max_words=max_content_words),
            })
        return articles, None
    except httpx.TimeoutException:
        return [], f"Timeout fetching {url}"
    except httpx.HTTPStatusError as e:
        return [], f"HTTP {e.response.status_code} at {url}"
    except Exception as e:
        return [], f"Error fetching {url}: {e}"


# ── HTML scraper ───────────────────────────────────────────────────────────────

async def fetch_scrape(
    client: httpx.AsyncClient,
    engineer: dict,
    source: dict,
    cutoff: datetime,
    max_content_words: int = 2000,
) -> tuple[list[dict], str | None]:
    """Scrape a page for article links."""
    url = source['url']
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        scrape_fn = scrape_generic
        for prefix, fn in _SCRAPERS.items():
            if url.startswith(prefix):
                scrape_fn = fn
                break
        raw_items = scrape_fn(soup, url)
        articles = [
            {
                'engineer': engineer['name'],
                'priority': engineer.get('priority', 99),
                'bio': engineer.get('bio', ''),
                'source_type': 'scrape',
                'source_label': source.get('label', ''),
                'source_url': url,
                **item,
            }
            for item in raw_items
        ]
        return articles, None
    except httpx.TimeoutException:
        return [], f"Timeout fetching {url}"
    except httpx.HTTPStatusError as e:
        return [], f"HTTP {e.response.status_code} at {url}"
    except Exception as e:
        return [], f"Error scraping {url}: {e}"


# ── Main fetcher ───────────────────────────────────────────────────────────────

async def fetch_all(
    sources_config: dict,
    cutoff: datetime,
    max_priority: int | None = None,
    max_content_words: int = 2000,
) -> tuple[list[dict], list[str], list[dict]]:
    """Run all rss/scrape fetches concurrently; collect chrome-only sources."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
    }
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        tasks = []
        task_meta = []
        chrome_sources = []
        for eng in sources_config.get('engineers', []):
            priority = eng.get('priority', 99)
            if max_priority is not None and priority > max_priority:
                continue
            for src in eng.get('sources', []):
                stype = src.get('type', 'skip')
                if stype == 'rss':
                    tasks.append(fetch_rss(client, eng, src, cutoff, max_content_words))
                    task_meta.append(eng)
                elif stype == 'scrape':
                    tasks.append(fetch_scrape(client, eng, src, cutoff, max_content_words))
                    task_meta.append(eng)
                elif stype == 'chrome':
                    chrome_sources.append({
                        'engineer': eng['name'],
                        'priority': priority,
                        'url': src['url'],
                        'label': src.get('label', ''),
                        'note': src.get('note', ''),
                    })
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: list[dict] = []
    errors: list[str] = []
    for i, result in enumerate(results):
        eng = task_meta[i]
        if isinstance(result, Exception):
            errors.append(f"{eng['name']}: {result}")
        else:
            articles, error = result
            all_articles.extend(articles)
            if error:
                errors.append(f"{eng['name']}: {error}")

    return all_articles, errors, chrome_sources


# ── Config helpers ─────────────────────────────────────────────────────────────

def load_sources(path: str) -> dict:
    """Load and parse digest_sources.yaml."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Sources file not found: {path}")
    with open(p) as f:
        return yaml.safe_load(f)


def build_engineer_index(sources_config: dict) -> dict:
    """Build a lowercase name → engineer config dict for O(1) lookup."""
    return {
        eng['name'].lower(): eng
        for eng in sources_config.get('engineers', [])
    }
