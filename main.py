"""SWE AI Digest — main pipeline entrypoint.

Usage:
    python main.py [--days N] [--dry-run] [--config PATH] [--no-email] [--no-feed]

Steps:
    1. Load config + secrets
    2. Set up logging
    3. Fetch articles from all RSS/scrape sources
    4. Filter already-processed articles via state
    5. Call Anthropic API (batch) to filter for AI relevance and summarise
    6. Save structured digest JSON to output/
    7. Send digest email to subscribers
    8. Update RSS feed (docs/feed.xml) and push to GitHub Pages
    9. Persist updated state
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
from email_sender.sender import send_admin_notification, send_digest
from feed.publisher import update_feed
from fetcher.core import dedup_by_url, fetch_all, load_sources, sort_and_filter_articles
from state.tracker import filter_new_articles, load_state, mark_processed, save_state


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the SWE AI Digest pipeline')
    parser.add_argument('--days', type=int, default=None,
                        help='Lookback window in days (overrides config)')
    parser.add_argument('--config', default='config.yaml',
                        help='Path to config file (default: config.yaml)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Fetch and process but do not send email or push feed')
    parser.add_argument('--no-email', action='store_true',
                        help='Skip sending email')
    parser.add_argument('--no-feed', action='store_true',
                        help='Skip updating RSS feed')
    return parser.parse_args()


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    _apply_env_overrides(cfg)
    return cfg


def _apply_env_overrides(cfg: dict) -> None:
    """Overlay email (and feed) config with environment variables.

    Env vars take precedence over config.yaml. This lets a single config file
    serve as a template while machine-specific values live in .env.

    Supported overrides:
        SMTP_HOST, SMTP_PORT, SMTP_FROM_ADDRESS, SMTP_FROM_NAME,
        SMTP_ADMIN_ADDRESS, SMTP_SUBSCRIBERS_FILE,
        FEED_LINK, FEED_PUBLISHER_NAME, FEED_PUBLISHER_EMAIL,
        ANTHROPIC_MODEL
    """
    email = cfg.setdefault('email', {})
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
            pass  # leave config.yaml value intact if env var is malformed


# ── Logging ────────────────────────────────────────────────────────────────────

def setup_logging(logs_dir: str) -> None:
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    iso = now.isocalendar()
    log_file = logs_path / f"run_{now.year}-CW{iso.week:02d}.log"

    fmt = '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
    logging.basicConfig(
        level=logging.INFO,
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


# ── Main pipeline ──────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    load_dotenv()

    cfg = load_config(args.config)
    setup_logging(cfg['paths']['logs_dir'])
    logger = logging.getLogger('main')

    lookback_days = args.days or cfg['pipeline']['lookback_days']
    now = datetime.now(tz=timezone.utc)
    period_end = now
    period_start = now - timedelta(days=lookback_days)

    logger.info("=== SWE AI Digest pipeline start ===")
    logger.info("Lookback: %d days (%s to %s)", lookback_days,
                period_start.date(), period_end.date())
    if args.dry_run:
        logger.info("DRY RUN — email and feed push will be skipped")

    # ── Secrets ────────────────────────────────────────────────────────────────
    anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    if not anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return 1

    # ── Load sources + state ───────────────────────────────────────────────────
    sources_config = load_sources(cfg['paths']['sources_yaml'])
    state = load_state(cfg['paths']['state_file'])
    logger.info("State: %d previously processed URLs", len(state['processed_urls']))

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

    # ── Filter already-seen ────────────────────────────────────────────────────
    new_articles = filter_new_articles(all_articles, state)
    logger.info("%d new articles (not yet processed)", len(new_articles))

    if not new_articles:
        logger.info("No new articles this week")
        if not args.dry_run and not args.no_email and smtp_password:
            send_admin_notification(
                reason="No new articles found",
                cfg=cfg,
                smtp_password=smtp_password,
                details=f"Lookback: {lookback_days} days. Fetch errors: {len(fetch_errors)}",
            )
        logger.info("=== Pipeline complete (no digest generated) ===")
        return 0

    # ── AI filtering + summarisation ───────────────────────────────────────────
    logger.info("Calling Anthropic API with %d articles...", len(new_articles))
    try:
        ai_result = call_anthropic(
            articles=new_articles,
            days=lookback_days,
            model=cfg['anthropic']['model'],
            max_tokens=cfg['anthropic']['max_tokens'],
            max_retries=cfg['anthropic']['max_retries'],
            api_key=anthropic_api_key,
        )
    except RuntimeError as e:
        logger.error("AI processing failed: %s", e)
        if not args.dry_run and not args.no_email and smtp_password:
            send_admin_notification(
                reason="Anthropic API failed",
                cfg=cfg,
                smtp_password=smtp_password,
                details=str(e),
            )
        return 1

    enriched_articles = enrich_ai_articles(ai_result, new_articles)
    logger.info("AI result: %d AI-relevant, %d dropped",
                len(enriched_articles), ai_result.get('dropped_count', 0))

    if not enriched_articles:
        logger.info("No AI-relevant articles found this week")
        if not args.dry_run and not args.no_email and smtp_password:
            send_admin_notification(
                reason="No AI-relevant articles found",
                cfg=cfg,
                smtp_password=smtp_password,
                details=(f"Scanned {len(new_articles)} new articles; "
                         f"all {ai_result.get('dropped_count', 0)} were dropped as not AI-relevant."),
            )
        state = mark_processed(state, new_articles)
        save_state(cfg['paths']['state_file'], state)
        logger.info("=== Pipeline complete (no AI-relevant content) ===")
        return 0

    # ── Build + save digest JSON ───────────────────────────────────────────────
    stats = {
        'articles_fetched': len(all_articles),
        'articles_new': len(new_articles),
        'articles_ai_related': len(enriched_articles),
        'articles_dropped': ai_result.get('dropped_count', 0),
        'blogs_unreachable': len(fetch_errors),
        'chrome_only_skipped': len(chrome_sources),
    }
    digest = build_digest(ai_result, enriched_articles, period_start, period_end, stats)
    digest_file = save_digest(digest, cfg['paths']['output_dir'])
    logger.info("Digest saved to %s", digest_file)

    # ── Email ──────────────────────────────────────────────────────────────────
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

    # ── RSS feed ───────────────────────────────────────────────────────────────
    if not args.dry_run and not args.no_feed:
        try:
            repo_path = str(Path(__file__).parent)
            update_feed(digest, cfg['feed'], repo_path)
        except Exception as e:
            logger.error("RSS feed update failed: %s", e)
    else:
        logger.info("RSS feed update skipped (dry-run or --no-feed)")

    # ── Persist state ──────────────────────────────────────────────────────────
    state = mark_processed(state, new_articles)
    save_state(cfg['paths']['state_file'], state)
    logger.info("State updated: %d total processed URLs", len(state['processed_urls']))

    logger.info("=== Pipeline complete: %d articles in digest ===", len(enriched_articles))
    return 0


if __name__ == '__main__':
    sys.exit(main())
