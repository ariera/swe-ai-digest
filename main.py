"""SWE AI Digest — main pipeline entrypoint.

Usage:
    python main.py [--days N] [--dry-run] [--config PATH] [--no-email] [--no-feed]
    python main.py --scheduled          # for hourly cron: runs only on scheduled days, skips if already done

Steps:
    1. (if --scheduled) Check if today is a run day and whether a successful run exists
    2. Load config + secrets
    3. Fetch articles from all RSS/scrape sources
    4. Call Anthropic API (batch) to filter for AI relevance and summarise
    5. Save structured digest JSON to output/
    6. Send digest email to subscribers (only on full success)
    7. Update RSS feed (docs/feed.xml) and push to GitHub Pages
    8. (if --scheduled) Write success marker
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from ai.digest import call_anthropic, enrich_ai_articles
from email_sender.sender import send_digest
from feed.publisher import push_docs, update_feed
from fetcher.core import dedup_by_url, fetch_all, load_sources, sort_and_filter_articles
from pages.builder import update_pages


REPO_ROOT = Path(__file__).parent
MARKER_FILE = REPO_ROOT / 'data' / '.last_success'


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the SWE AI Digest pipeline')
    parser.add_argument('--days', type=int, default=None,
                        help='Lookback window in days (overrides config)')
    parser.add_argument('--config', default=str(REPO_ROOT / 'config.yaml'),
                        help='Path to config file (default: config.yaml next to main.py)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Fetch and process but do not call AI, send email, or push feed')
    parser.add_argument('--no-email', action='store_true',
                        help='Skip sending email')
    parser.add_argument('--no-feed', action='store_true',
                        help='Skip updating RSS feed')
    parser.add_argument('--scheduled', action='store_true',
                        help='Scheduled mode: only run on configured days, skip if already succeeded today')
    parser.add_argument('--debug', action='store_true',
                        help='Set log level to DEBUG (default: INFO)')
    return parser.parse_args()


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    _resolve_paths(cfg)
    _apply_env_overrides(cfg)
    return cfg


def _resolve_paths(cfg: dict) -> None:
    """Make all paths in config absolute relative to the repo root."""
    paths = cfg.setdefault('paths', {})
    for key, value in paths.items():
        if value and not Path(value).is_absolute():
            paths[key] = str(REPO_ROOT / value)
    email = cfg.setdefault('email', {})
    sub_file = email.get('subscribers_file', '')
    if sub_file and not Path(sub_file).is_absolute():
        email['subscribers_file'] = str(REPO_ROOT / sub_file)


def _apply_env_overrides(cfg: dict) -> None:
    """Overlay config with environment variables."""
    email = cfg.setdefault('email', {})
    _env_str(email, 'backend', 'EMAIL_BACKEND')
    _env_str(email, 'smtp_host', 'SMTP_HOST')
    _env_int(email, 'smtp_port', 'SMTP_PORT')
    _env_str(email, 'from_address', 'SMTP_FROM_ADDRESS')
    _env_str(email, 'from_name', 'SMTP_FROM_NAME')
    _env_str(email, 'admin_address', 'SMTP_ADMIN_ADDRESS')
    _env_str(email, 'subscribers_file', 'SMTP_SUBSCRIBERS_FILE')

    feed = cfg.setdefault('feed', {})
    _env_str(feed, 'link', 'FEED_LINK')
    _env_str(feed, 'publisher_name', 'FEED_PUBLISHER_NAME')
    _env_str(feed, 'publisher_email', 'FEED_PUBLISHER_EMAIL')

    anthropic = cfg.setdefault('anthropic', {})
    _env_str(anthropic, 'model', 'ANTHROPIC_MODEL')


def _env_str(section: dict, key: str, env_var: str) -> None:
    value = os.environ.get(env_var)
    if value is not None:
        section[key] = value


def _env_int(section: dict, key: str, env_var: str) -> None:
    value = os.environ.get(env_var)
    if value is not None:
        try:
            section[key] = int(value)
        except ValueError:
            pass


# ── Scheduling ─────────────────────────────────────────────────────────────────

def _should_run(cfg: dict, now: datetime) -> tuple[bool, str]:
    """Check whether a scheduled run should proceed.

    Returns (True, '') if all conditions are met, or (False, reason) otherwise.

    On a scheduled day: run if past the configured hour and not already done today.
    On any other day: run as a catch-up if no successful run exists for this ISO week
    (handles Monday failures that need to retry on Tuesday, Wednesday, etc.).
    """
    schedule = cfg['pipeline'].get('schedule', {})
    days = schedule.get('days', [1])           # default Mon
    hour = schedule.get('hour', 7)            # default 07:00 UTC

    today_weekday = now.isoweekday()
    if today_weekday in days:
        # Normal scheduled day
        if now.hour < hour:
            return False, f"current hour {now.hour} UTC is before scheduled hour {hour}"
        if _already_succeeded_today(now):
            return False, "already succeeded today"
        return True, ''
    else:
        # Off-day: only run if this week hasn't had a successful run yet
        if _already_succeeded_this_week(now):
            return False, f"today is weekday {today_weekday} (not a scheduled day) and already succeeded this week"
        return True, f"catch-up run: weekday {today_weekday} is not a scheduled day but no successful run this week"


def _already_succeeded_today(now: datetime) -> bool:
    """Check if the success marker shows today's date."""
    if not MARKER_FILE.exists():
        return False
    try:
        content = MARKER_FILE.read_text().strip()
        return content == now.strftime('%Y-%m-%d')
    except OSError:
        return False


def _already_succeeded_this_week(now: datetime) -> bool:
    """Check if the success marker is from the current ISO week."""
    if not MARKER_FILE.exists():
        return False
    try:
        content = MARKER_FILE.read_text().strip()
        marker_date = datetime.strptime(content, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        return marker_date.isocalendar()[:2] == now.isocalendar()[:2]  # (year, week)
    except (OSError, ValueError):
        return False


def _write_success_marker(now: datetime) -> None:
    MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARKER_FILE.write_text(now.strftime('%Y-%m-%d'))


# ── Logging ────────────────────────────────────────────────────────────────────

def setup_logging(logs_dir: str, debug: bool = False) -> None:
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    iso = now.isocalendar()
    log_file = logs_path / f"run_{now.year}-CW{iso.week:02d}.log"

    fmt = '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── Digest JSON ────────────────────────────────────────────────────────────────

def build_digest(
    ai_result: dict,
    enriched_articles: list[dict],
    period_start: datetime,
    period_end: datetime,
    stats: dict,
) -> dict:
    return {
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
        'period_start': period_start.isoformat(),
        'period_end': period_end.isoformat(),
        'global_summary': ai_result['global_summary'],
        'stats': stats,
        'articles': enriched_articles,
    }


def save_digest(digest: dict, output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    iso = now.isocalendar()
    filename = f"digest_{now.year}-CW{iso.week:02d}.json"
    out_file = output_path / filename
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(digest, f, indent=2, ensure_ascii=False)
    return out_file


# ── Dry-run mock ──────────────────────────────────────────────────────────────

def _mock_ai_result(articles: list[dict]) -> dict:
    """Return a plausible AI result without calling the API."""
    sample = articles[:3]
    dropped = len(articles) - len(sample)
    return {
        'global_summary': (
            '[DRY RUN] This is a mock global summary. '
            f'{len(sample)} article(s) were selected from {len(articles)} fetched '
            f'as a dry-run sample. No actual AI filtering was performed.'
        ),
        'articles': [
            {
                'url': a['url'],
                'summary': (
                    f'[DRY RUN] Mock summary for "{a["title"]}" by {a["engineer"]}. '
                    'No actual AI summarisation was performed.'
                ),
            }
            for a in sample
        ],
        'dropped_count': dropped,
    }


# ── Main pipeline ──────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    load_dotenv()

    cfg = load_config(args.config)
    setup_logging(cfg['paths']['logs_dir'], debug=args.debug)
    logger = logging.getLogger('main')

    now = datetime.now(tz=timezone.utc)

    # ── Scheduling guard ──────────────────────────────────────────────────────
    if args.scheduled:
        should, reason = _should_run(cfg, now)
        if not should:
            logger.debug("Scheduled run skipped: %s", reason)
            return 0
        logger.info("Scheduled run triggered (day=%d, hour=%d)", now.isoweekday(), now.hour)

    lookback_days = args.days or cfg['pipeline']['lookback_days']
    period_end = now
    period_start = now - timedelta(days=lookback_days)

    logger.info("=== SWE AI Digest pipeline start ===")
    logger.info("Lookback: %d days (%s to %s)", lookback_days,
                period_start.date(), period_end.date())
    if args.dry_run:
        logger.info("DRY RUN — AI call will be mocked; email and feed push will be skipped")

    # ── Secrets ────────────────────────────────────────────────────────────────
    anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    if not anthropic_api_key and not args.dry_run:
        logger.error("ANTHROPIC_API_KEY not set")
        return 1

    # ── Load sources ──────────────────────────────────────────────────────────
    sources_config = load_sources(cfg['paths']['sources_yaml'])

    # ── Fetch ──────────────────────────────────────────────────────────────────
    logger.info("Fetching articles from RSS feeds and scrapers...")
    all_articles, fetch_errors, chrome_sources = asyncio.run(
        fetch_all(
            sources_config=sources_config,
            cutoff=period_start,
            max_content_words=cfg['pipeline']['max_article_words'],
        )
    )

    for err in fetch_errors:
        logger.warning("Fetch error: %s", err)

    all_articles = dedup_by_url(all_articles)
    all_articles = sort_and_filter_articles(all_articles)
    logger.info("Fetched %d articles total (%d fetch errors, %d chrome-only sources skipped)",
                len(all_articles), len(fetch_errors), len(chrome_sources))

    if not all_articles:
        logger.info("No articles found — nothing to do")
        logger.info("=== Pipeline complete (no articles) ===")
        return 0

    # ── AI filtering + summarisation ───────────────────────────────────────────
    if args.dry_run:
        logger.info("DRY RUN — skipping Anthropic API call, using mock result")
        ai_result = _mock_ai_result(all_articles)
    else:
        logger.info("Calling Anthropic API with %d articles...", len(all_articles))
        try:
            ai_result = call_anthropic(
                articles=all_articles,
                days=lookback_days,
                model=cfg['anthropic']['model'],
                max_tokens=cfg['anthropic']['max_tokens'],
                max_retries=cfg['anthropic']['max_retries'],
                api_key=anthropic_api_key,
            )
        except RuntimeError as e:
            logger.error("AI processing failed: %s", e)
            logger.error("=== Pipeline FAILED — no email, feed, or pages updated ===")
            return 1

    enriched_articles = enrich_ai_articles(ai_result, all_articles)
    logger.info("AI result: %d AI-relevant, %d dropped",
                len(enriched_articles), ai_result.get('dropped_count', 0))

    if not enriched_articles:
        logger.info("No AI-relevant articles found this week — nothing to publish")
        logger.info("=== Pipeline complete (no AI-relevant content) ===")
        return 0

    # ── Build + save digest JSON ───────────────────────────────────────────────
    stats = {
        'articles_fetched': len(all_articles),
        'articles_ai_related': len(enriched_articles),
        'articles_dropped': ai_result.get('dropped_count', 0),
        'blogs_unreachable': len(fetch_errors),
        'chrome_only_skipped': len(chrome_sources),
    }
    digest = build_digest(ai_result, enriched_articles, period_start, period_end, stats)
    digest_file = save_digest(digest, cfg['paths']['output_dir'])
    logger.info("Digest saved to %s", digest_file)

    # ── Email (only on full success) ──────────────────────────────────────────
    if not args.dry_run and not args.no_email:
        if not smtp_password:
            logger.warning("SMTP_PASSWORD not set — skipping email")
        else:
            try:
                send_digest(digest, cfg, smtp_password)
            except Exception as e:
                logger.error("Email delivery failed: %s", e)
    else:
        logger.info("Email skipped (dry-run or --no-email)")

    # ── RSS feed + pages + push ────────────────────────────────────────────────
    if not args.dry_run and not args.no_feed:
        repo_path = str(REPO_ROOT)
        docs_dir = str(REPO_ROOT / 'docs')
        try:
            update_feed(digest, cfg['feed'], repo_path)
        except Exception as e:
            logger.error("RSS feed update failed: %s", e)
        try:
            update_pages(digest, docs_dir)
        except Exception as e:
            logger.error("Pages build failed: %s", e)
        if cfg['feed'].get('auto_push', False):
            try:
                week = now.isocalendar()
                push_docs(repo_path, f"digest: CW{week.week} {week.year}")
            except Exception as e:
                logger.error("Git push failed: %s", e)
    else:
        logger.info("RSS feed and pages update skipped (dry-run or --no-feed)")

    # ── Success marker ─────────────────────────────────────────────────────────
    if args.scheduled and not args.dry_run:
        _write_success_marker(now)
        logger.info("Success marker written — won't re-run today")

    logger.info("=== Pipeline complete: %d articles in digest ===", len(enriched_articles))
    return 0


if __name__ == '__main__':
    sys.exit(main())
