"""RSS 2.0 feed publisher.

Maintains an ever-growing docs/feed.xml with AI-filtered digest articles,
sorted newest-first by original publication date. After updating the file,
optionally commits and pushes to GitHub so GitHub Pages serves the feed.
"""

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

RSS_DATE_FMT = '%a, %d %b %Y %H:%M:%S +0000'


# ── RSS date helpers ───────────────────────────────────────────────────────────

def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _to_rss_date(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return datetime.now(tz=timezone.utc).strftime(RSS_DATE_FMT)
    return dt.strftime(RSS_DATE_FMT)


# ── Feed read / write ──────────────────────────────────────────────────────────

def _load_existing_items(feed_path: Path) -> list[dict]:
    """Parse existing feed.xml and return items as dicts. Returns [] if missing."""
    if not feed_path.exists():
        return []
    try:
        tree = ET.parse(feed_path)
        root = tree.getroot()
        channel = root.find('channel')
        if channel is None:
            return []
        items = []
        for item in channel.findall('item'):
            def text(tag: str) -> str:
                el = item.find(tag)
                return el.text or '' if el is not None else ''
            items.append({
                'title': text('title'),
                'url': text('link'),
                'author': text('author'),
                'pub_date': text('pubDate'),
                'description': text('description'),
                'guid': text('guid'),
            })
        return items
    except ET.ParseError as e:
        logger.warning("Could not parse existing feed.xml: %s — starting fresh", e)
        return []


def _build_feed_xml(channel_cfg: dict, items: list[dict]) -> str:
    """Build RSS 2.0 XML string from channel config and items list."""
    rss = ET.Element('rss', version='2.0')
    rss.set('xmlns:atom', 'http://www.w3.org/2005/Atom')
    channel = ET.SubElement(rss, 'channel')

    def add(parent: ET.Element, tag: str, text: str) -> None:
        el = ET.SubElement(parent, tag)
        el.text = text

    add(channel, 'title', channel_cfg['title'])
    add(channel, 'link', channel_cfg['link'])
    add(channel, 'description', channel_cfg['description'])
    add(channel, 'language', 'en-us')
    add(channel, 'lastBuildDate', datetime.now(tz=timezone.utc).strftime(RSS_DATE_FMT))

    atom_link = ET.SubElement(channel, 'atom:link')
    atom_link.set('href', channel_cfg['link'])
    atom_link.set('rel', 'self')
    atom_link.set('type', 'application/rss+xml')

    managing_editor = f"{channel_cfg['publisher_email']} ({channel_cfg['publisher_name']})"
    add(channel, 'managingEditor', managing_editor)

    for item_data in items:
        item = ET.SubElement(channel, 'item')
        add(item, 'title', item_data['title'])
        add(item, 'link', item_data['url'])
        add(item, 'author', item_data['author'])
        add(item, 'pubDate', item_data['pub_date'])
        add(item, 'description', item_data['description'])
        guid = ET.SubElement(item, 'guid')
        guid.set('isPermaLink', 'true')
        guid.text = item_data['url']

    ET.indent(rss, space='  ')
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding='unicode')


# ── Public API ─────────────────────────────────────────────────────────────────

def update_feed(digest: dict, feed_cfg: dict, repo_path: str) -> None:
    """Append new digest articles to the RSS feed and optionally push to GitHub.

    The feed is ever-growing: existing items are preserved and new items are
    prepended (newest first). Items are sorted by publication date descending.
    """
    feed_path = Path(repo_path) / feed_cfg['output_path']
    feed_path.parent.mkdir(parents=True, exist_ok=True)

    existing_items = _load_existing_items(feed_path)
    existing_urls = {item['url'] for item in existing_items}

    new_items = []
    for article in digest.get('articles', []):
        if article['url'] in existing_urls:
            continue
        new_items.append({
            'title': article['title'],
            'url': article['url'],
            'author': article['author'],
            'pub_date': _to_rss_date(article.get('published_at')),
            'description': article['summary'],
            'guid': article['url'],
        })

    if not new_items:
        logger.info("No new items to add to RSS feed")
        return

    logger.info("Adding %d new item(s) to RSS feed", len(new_items))
    all_items = new_items + existing_items

    # Sort newest-first by pub_date
    def _sort_key(item: dict) -> datetime:
        try:
            return datetime.strptime(item['pub_date'], RSS_DATE_FMT).replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    all_items.sort(key=_sort_key, reverse=True)

    channel_cfg = {
        'title': feed_cfg['title'],
        'link': feed_cfg['link'],
        'description': feed_cfg['description'],
        'publisher_name': feed_cfg['publisher_name'],
        'publisher_email': feed_cfg['publisher_email'],
    }
    xml_content = _build_feed_xml(channel_cfg, all_items)

    feed_path.write_text(xml_content, encoding='utf-8')
    logger.info("RSS feed written to %s (%d total items)", feed_path, len(all_items))

    if feed_cfg.get('auto_push', False):
        _git_push_feed(feed_path, repo_path, len(new_items))


def _git_push_feed(feed_path: Path, repo_path: str, new_count: int) -> None:
    """Commit and push all docs/ changes (feed, pages, manifest) to GitHub."""
    week = datetime.now(tz=timezone.utc).isocalendar()
    commit_msg = f"digest: CW{week.week} {week.year} — {new_count} new item(s)"
    try:
        subprocess.run(['git', 'add', 'docs/'], cwd=repo_path, check=True, capture_output=True)
        result = subprocess.run(
            ['git', 'diff', '--cached', '--quiet'],
            cwd=repo_path,
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info("No changes to feed.xml — skipping git push")
            return
        subprocess.run(
            ['git', 'commit', '-m', commit_msg],
            cwd=repo_path, check=True, capture_output=True,
        )
        subprocess.run(['git', 'push'], cwd=repo_path, check=True, capture_output=True)
        logger.info("RSS feed pushed to GitHub: %s", commit_msg)
    except subprocess.CalledProcessError as e:
        logger.error("Git push failed: %s\nstdout: %s\nstderr: %s",
                     e, e.stdout.decode() if e.stdout else '', e.stderr.decode() if e.stderr else '')
        raise
