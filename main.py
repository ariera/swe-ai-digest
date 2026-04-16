"""SWE AI Digest — main pipeline entrypoint.

Usage:
    python main.py feed   [--dry-run] [--debug] [--config PATH]
    python main.py digest [--dry-run] [--no-email] [--admin-only] [--force]
                          [--calendar-week | --week YYYY-CWnn | --days N] [--debug] [--config PATH]

Subcommands:
    feed    Fetch articles, AI filter/summarize, update RSS feed (hourly cron)
    digest  Build and send weekly digest email (weekly cron, self-guarding)
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from ai.digest import generate_global_summary, summarize_article
from db.models import Article, Author, Digest, Source, sync_sources_from_yaml
from db.session import get_engine, get_session_factory, init_db
from email_sender.sender import send_digest
from feed.publisher import push_docs, update_feed
from fetcher.core import fetch_all, load_sources
from pages.builder import update_pages


REPO_ROOT = Path(__file__).parent
logger = logging.getLogger('main')


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='SWE AI Digest pipeline')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # -- feed subcommand --
    feed_parser = subparsers.add_parser('feed', help='Fetch, AI summarize, update RSS feed')
    feed_parser.add_argument('--dry-run', action='store_true',
                             help='Read-only: fetch and report, no DB writes / AI / feed updates')
    feed_parser.add_argument('--debug', action='store_true', help='Set log level to DEBUG')
    feed_parser.add_argument('--config', default=str(REPO_ROOT / 'config.yaml'),
                             help='Path to config file')

    # -- digest subcommand --
    digest_parser = subparsers.add_parser('digest', help='Build and send weekly digest')
    digest_parser.add_argument('--dry-run', action='store_true',
                               help='Read-only: report what digest would contain, no changes')
    digest_parser.add_argument('--no-email', action='store_true',
                               help='Full pipeline but skip email send')
    digest_parser.add_argument('--admin-only', action='store_true',
                               help='Send email to admin address only')
    digest_parser.add_argument('--force', action='store_true',
                               help='Skip already-sent guard, re-create digest for this period')
    period_group = digest_parser.add_mutually_exclusive_group()
    period_group.add_argument('--calendar-week', action='store_true',
                              help='Use previous ISO calendar week (Mon–Sun)')
    period_group.add_argument('--week', metavar='YYYY-CWnn',
                              type=_parse_week_arg,
                              help='Target a specific ISO week, e.g. 2026-CW15')
    period_group.add_argument('--days', type=int, default=None,
                              help='Lookback window in days (overrides config)')
    digest_parser.add_argument('--debug', action='store_true', help='Set log level to DEBUG')
    digest_parser.add_argument('--config', default=str(REPO_ROOT / 'config.yaml'),
                               help='Path to config file')

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


# ── Shared setup ──────────────────────────────────────────────────────────────

def _setup(args: argparse.Namespace) -> dict:
    """Common init: dotenv, config, logging, DB. Returns config dict."""
    load_dotenv()
    cfg = load_config(args.config)
    setup_logging(cfg['paths']['logs_dir'], debug=args.debug)
    db_path = cfg['paths']['db_path']
    engine = get_engine(db_path)
    init_db(engine)
    cfg['_engine'] = engine
    cfg['_session_factory'] = get_session_factory(engine)
    return cfg


# ── Period calculation ─────────────────────────────────────────────────────────

def _parse_week_arg(value: str) -> tuple[int, int]:
    """Validate and parse a '--week YYYY-CWnn' argument into (year, week)."""
    import re
    m = re.fullmatch(r'(\d{4})-CW(\d{1,2})', value, re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(
            f"Invalid format: {value!r}. Expected YYYY-CWnn (e.g. 2026-CW15)"
        )
    return int(m.group(1)), int(m.group(2))


def _iso_week_bounds(year: int, week: int) -> tuple[datetime, datetime]:
    """Return (monday_00:00, sunday_23:59:59) UTC for the given ISO year/week."""
    from datetime import date
    monday = date.fromisocalendar(year, week, 1)
    monday_dt = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    sunday_dt = monday_dt + timedelta(weeks=1) - timedelta(seconds=1)
    return monday_dt, sunday_dt


def _previous_iso_week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (monday_00:00, sunday_23:59:59) UTC for the ISO week before now's week."""
    current_monday = now - timedelta(days=now.isoweekday() - 1)
    current_monday = current_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    prev_monday = current_monday - timedelta(weeks=1)
    prev_sunday = current_monday - timedelta(seconds=1)
    return prev_monday, prev_sunday


def _compute_period(args: argparse.Namespace, cfg: dict, now: datetime) -> tuple[datetime, datetime, str]:
    """Compute (period_start, period_end, label) based on CLI flags and config.

    CLI flags take precedence: --days overrides calendar_week config.
    """
    if getattr(args, 'week', None):
        year, week = args.week
        period_start, period_end = _iso_week_bounds(year, week)
        label = f"{year}-CW{week:02d}"
        return period_start, period_end, label
    if getattr(args, 'days', None):
        # Explicit --days flag always wins
        period_end = now
        period_start = now - timedelta(days=args.days)
        label = f"{now.year}-custom-{args.days}d"
        return period_start, period_end, label
    use_calendar_week = getattr(args, 'calendar_week', False) or cfg['pipeline'].get('lookback_mode') == 'calendar_week'
    if use_calendar_week:
        period_start, period_end = _previous_iso_week_bounds(now)
        iso = period_start.isocalendar()
        label = f"{iso.year}-CW{iso.week:02d}"
    elif args.days:
        period_end = now
        period_start = now - timedelta(days=args.days)
        label = f"{now.year}-custom-{args.days}d"
    else:
        lookback_days = cfg['pipeline']['lookback_days']
        period_end = now
        period_start = now - timedelta(days=lookback_days)
        label = f"{now.year}-custom-{lookback_days}d"
    return period_start, period_end, label


# ── feed command ──────────────────────────────────────────────────────────────

def cmd_feed(args: argparse.Namespace) -> int:
    cfg = _setup(args)
    session = cfg['_session_factory']()
    now = datetime.now(tz=timezone.utc)

    logger.info("=== feed start ===")

    if args.dry_run:
        logger.info("DRY RUN — fetch and report only, no DB/AI/feed changes")

    # 1. Sync YAML → DB
    sources_config = load_sources(cfg['paths']['sources_yaml'])
    if not args.dry_run:
        sync_sources_from_yaml(session, sources_config)
        logger.info("Synced authors + sources from YAML")

    # 2. Compute cutoff for fetching
    use_calendar_week = cfg['pipeline'].get('lookback_mode') == 'calendar_week'
    if use_calendar_week:
        period_start, _ = _previous_iso_week_bounds(now)
    else:
        lookback_days = cfg['pipeline']['lookback_days']
        period_start = now - timedelta(days=lookback_days)

    # 3. Fetch all enabled sources
    logger.info("Fetching articles...")
    all_articles, fetch_errors, chrome_sources = asyncio.run(
        fetch_all(
            sources_config=sources_config,
            cutoff=period_start,
            max_content_words=cfg['pipeline']['max_article_words'],
        )
    )
    for err in fetch_errors:
        logger.warning("Fetch error: %s", err)
    logger.info("Fetched %d articles (%d errors, %d chrome-only skipped)",
                len(all_articles), len(fetch_errors), len(chrome_sources))

    if args.dry_run:
        logger.info("=== feed dry-run complete ===")
        session.close()
        return 0

    # 4. Record fetch health on sources
    _record_fetch_health(session, sources_config, fetch_errors)

    # 5. Insert articles into DB (dedup via URL unique constraint)
    new_count = 0
    for a in all_articles:
        # Podcast articles carry their own slug; regular articles need a name lookup
        author_slug = a.get('slug') or _engineer_name_to_slug(sources_config, a.get('engineer', ''))
        author = session.query(Author).filter_by(slug=author_slug).first() if author_slug else None
        if author is None:
            continue
        source_url = a.get('source_url', '')
        source = session.query(Source).filter_by(url=source_url).first()
        source_type = a.get('source_type')
        attribution = (
            f"Featured in {a['source_label']}"
            if source_type == 'podcast' and a.get('source_label')
            else None
        )
        article = Article.insert_if_new(
            session,
            url=a['url'],
            author_id=author.id,
            source_id=source.id if source else None,
            title=a['title'],
            published_at=a.get('published'),
            raw_content=a.get('content'),
            source_type=source_type,
            attribution=attribution,
        )
        if article is not None:
            new_count += 1
            logger.debug("Inserted: [%s] %s", a.get('engineer', a.get('slug', '?')), a['url'])
        else:
            logger.debug("Skipped (duplicate): [%s] %s", a.get('engineer', a.get('slug', '?')), a['url'])
    session.commit()
    logger.info("Inserted %d new articles (%d already in DB)", new_count, len(all_articles) - new_count)

    # 6. AI processing: summarize unprocessed articles
    anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot process articles")
        session.close()
        return 1

    unprocessed = Article.unprocessed(session)
    logger.info("AI processing: %d unprocessed articles", len(unprocessed))
    for article in unprocessed:
        article_dict = {
            'author': article.author.name,
            'title': article.title,
            'url': article.url,
            'published_at': article.published_at,
            'content': article.raw_content,
            'source_type': article.source_type,
            'attribution': article.attribution,
        }
        try:
            result = summarize_article(
                article_dict,
                model=cfg['anthropic']['model'],
                max_tokens=cfg['anthropic']['max_tokens'],
                max_retries=cfg['anthropic']['max_retries'],
                api_key=anthropic_api_key,
                source_type=article.source_type,
            )
            article.mark_ai_result(session, summary=result.get('summary'), ai_relevant=result['ai_relevant'])
            session.commit()
        except RuntimeError as e:
            logger.warning("AI failed for %s: %s — will retry next run", article.url, e)

    # 7. Update feed.xml with unfed relevant articles
    unfed = Article.unfed(session)
    if unfed:
        feed_digest = _build_feed_digest(unfed)
        repo_path = str(REPO_ROOT)
        try:
            update_feed(feed_digest, cfg['feed'], repo_path)
            for article in unfed:
                article.mark_fed(session)
            session.commit()
            logger.info("Feed updated with %d new articles", len(unfed))
        except Exception as e:
            logger.error("RSS feed update failed: %s", e)

        # 8. Git push if auto_push
        if cfg['feed'].get('auto_push', False):
            try:
                week = now.isocalendar()
                push_docs(str(REPO_ROOT), f"feed: {len(unfed)} articles, CW{week.week} {week.year}")
            except Exception as e:
                logger.error("Git push failed: %s", e)
    else:
        logger.info("No new articles for feed")

    logger.info("=== feed complete ===")
    session.close()
    return 0


# ── digest command ────────────────────────────────────────────────────────────

def cmd_digest(args: argparse.Namespace) -> int:
    cfg = _setup(args)
    session = cfg['_session_factory']()
    now = datetime.now(tz=timezone.utc)

    logger.info("=== digest start ===")

    if args.dry_run:
        logger.info("DRY RUN — report only, no DB/AI/email/page changes")

    # 1. Compute period
    period_start, period_end, label = _compute_period(args, cfg, now)
    logger.info("Digest period: %s (%s to %s)", label, period_start.date(), period_end.date())

    # 2. Already-sent guard
    if not args.force and not args.dry_run:
        existing = Digest.for_period(session, period_start.isoformat(), period_end.isoformat())
        if existing is not None:
            logger.info("Digest already sent for this period (%s) — skipping. Use --force to override.", existing.label)
            session.close()
            return 0

    # 3. Run a feed pass to catch stragglers
    logger.info("Running feed pass to catch stragglers...")
    try:
        feed_args = argparse.Namespace(
            command='feed',
            dry_run=args.dry_run,
            debug=args.debug,
            config=args.config,
        )
        cmd_feed(feed_args)
    except Exception as e:
        logger.warning("Feed pass failed (continuing with existing data): %s", e)

    if args.dry_run:
        # Re-open session (feed pass closed it)
        session = cfg['_session_factory']()
        articles = Article.for_digest(session, period_start.isoformat(), period_end.isoformat())
        logger.info("DRY RUN — digest would contain %d articles", len(articles))
        for a in articles:
            logger.info("  - [%s] %s", a.author.name, a.title)
        logger.info("=== digest dry-run complete ===")
        session.close()
        return 0

    # Re-open session (feed pass closed it)
    session = cfg['_session_factory']()

    # 4. Check source health
    total, errored = Source.fetch_health(session)
    if total > 0 and errored / total > 0.5:
        logger.warning("HIGH FAILURE RATE: %d/%d enabled sources have errors", errored, total)
        _send_health_alert(cfg, total, errored)

    # 5. Gather articles for digest
    # Use current time as upper bound — the feed pass may have inserted articles after
    # the original period_end was computed
    query_end = datetime.now(tz=timezone.utc)
    articles = Article.for_digest(session, period_start.isoformat(), query_end.isoformat())
    if not articles:
        logger.info("No AI-relevant articles for this period — nothing to digest")
        logger.info("=== digest complete (no articles) ===")
        session.close()
        return 0
    logger.info("Digest has %d AI-relevant articles", len(articles))

    # 6. Generate global summary
    anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        session.close()
        return 1

    articles_for_summary = [a.to_dict() for a in articles]
    try:
        global_summary = generate_global_summary(
            articles_for_summary,
            model=cfg['anthropic']['model'],
            max_tokens=cfg['anthropic']['max_tokens'],
            max_retries=cfg['anthropic']['max_retries'],
            api_key=anthropic_api_key,
        )
    except RuntimeError as e:
        logger.error("Global summary generation failed: %s", e)
        session.close()
        return 1

    # 7. Create digest in DB (delete existing row first when --force)
    existing = Digest.for_period(session, period_start.isoformat(), period_end.isoformat())
    if existing is not None:
        session.execute(
            __import__('sqlalchemy').text(
                'DELETE FROM digest_articles WHERE digest_id = :did'
            ),
            {'did': existing.id},
        )
        session.flush()
        session.delete(existing)
        session.flush()
    digest_row = Digest.create(
        session, label=label,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        global_summary=global_summary,
        articles=articles,
    )
    session.commit()

    # 8. Build digest dict for templates
    digest_dict = digest_row.to_dict()
    digest_dict['generated_at'] = now.isoformat()
    digest_dict['period_start'] = period_start.isoformat()
    digest_dict['period_end'] = period_end.isoformat()

    # 9. Write digest page + update index
    docs_dir = str(REPO_ROOT / 'docs')
    try:
        update_pages(digest_dict, docs_dir)
        digest_row.page_at = now.isoformat()
        session.commit()
        logger.info("Digest page written")
    except Exception as e:
        logger.error("Pages build failed: %s", e)

    # 10. Send email
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    if not args.no_email:
        if not smtp_password:
            logger.warning("SMTP_PASSWORD not set — skipping email")
        else:
            if args.admin_only:
                logger.info("--admin-only: sending to admin address only")
            try:
                send_digest(digest_dict, cfg, smtp_password, admin_only=args.admin_only)
                digest_row.emailed_at = now.isoformat()
                session.commit()
                logger.info("Digest email sent")
            except Exception as e:
                logger.error("Email delivery failed: %s", e)
    else:
        logger.info("Email skipped (--no-email)")

    # 11. Git push
    if cfg['feed'].get('auto_push', False):
        try:
            push_docs(str(REPO_ROOT), f"digest: {label}")
        except Exception as e:
            logger.error("Git push failed: %s", e)

    logger.info("=== digest complete: %d articles ===", len(articles))
    session.close()
    return 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _engineer_name_to_slug(sources_config: dict, name: str) -> str | None:
    """Look up the slug for an engineer name from the YAML config."""
    for eng in sources_config.get('engineers', []):
        if eng['name'] == name:
            return eng.get('slug')
    return None


def _record_fetch_health(session, sources_config: dict, fetch_errors: list[str]) -> None:
    """Update source health after a fetch run."""
    error_names = set()
    for err in fetch_errors:
        name = err.split(':')[0].strip()
        error_names.add(name)

    for eng in sources_config.get('engineers', []):
        slug = eng.get('slug')
        if not slug:
            continue
        author = session.query(Author).filter_by(slug=slug).first()
        if not author:
            continue
        for src in eng.get('sources', []):
            source = session.query(Source).filter_by(url=src['url']).first()
            if source and source.enabled:
                error_msg = None
                if eng['name'] in error_names:
                    error_msg = f"Fetch error for {eng['name']}"
                source.record_fetch(session, error=error_msg)
    session.commit()


def _build_feed_digest(articles: list) -> dict:
    """Build a minimal digest dict for update_feed() from Article model instances."""
    return {
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
        'articles': [a.to_dict() for a in articles],
    }


def _send_health_alert(cfg: dict, total: int, errored: int) -> None:
    """Send a health alert email to admin if source failure rate is high."""
    admin_address = cfg.get('email', {}).get('admin_address')
    if not admin_address:
        logger.warning("No admin_address configured — cannot send health alert")
        return
    logger.warning("Health alert: %d/%d sources errored. Admin: %s", errored, total, admin_address)
    # TODO: implement actual email alert to admin


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()
    if args.command == 'feed':
        return cmd_feed(args)
    elif args.command == 'digest':
        return cmd_digest(args)
    return 1


if __name__ == '__main__':
    sys.exit(main())
