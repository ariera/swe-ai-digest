"""SMTP email sender for the SWE AI Digest."""

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / 'templates'
_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(['html']),
)


# ── Subscriber loading ─────────────────────────────────────────────────────────

def load_subscribers(path: str) -> list[dict]:
    """Load subscriber list from YAML. Returns empty list if file missing."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data.get('subscribers', []) if data else []
    except FileNotFoundError:
        logger.warning("Subscribers file not found: %s", path)
        return []


# ── Email rendering ────────────────────────────────────────────────────────────

def _week_label(dt: datetime) -> str:
    """Return 'CW14 2026' style label for the given datetime."""
    iso = dt.isocalendar()
    return f"CW{iso.week} {iso.year}"


def _digest_page_url(feed_link: str, dt: datetime) -> str:
    """Derive the digest page URL from the feed link and run datetime.

    feed_link: https://ariera.github.io/swe-ai-digest/feed.xml
    result:    https://ariera.github.io/swe-ai-digest/digests/2026-CW15.html
    """
    iso = dt.isocalendar()
    cw_slug = f"{iso.year}-CW{iso.week:02d}"
    base = feed_link.rsplit('/', 1)[0]  # strip feed.xml
    return f"{base}/digests/{cw_slug}.html"


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


def render_digest_email(digest: dict, feed_link: str = '') -> tuple[str, str, str]:
    """Render the digest as (subject, plain_text, html)."""
    now = datetime.now(tz=timezone.utc)
    week_label = _week_label(now)
    subject = f"SWE AI Digest — {week_label}"

    ctx = {
        'week_label': week_label,
        'period_start': digest.get('period_start', '')[:10],
        'period_end': digest.get('period_end', '')[:10],
        'global_summary': digest.get('global_summary', ''),
        'authors': _group_by_author(digest.get('articles', [])),
        'page_url': _digest_page_url(feed_link, now) if feed_link else '',
    }

    html = _jinja_env.get_template('email.html').render(**ctx)

    # Plain text uses a non-autoescaping environment
    plain_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    plain = plain_env.get_template('email.txt').render(**ctx)

    return subject, plain, html


# ── Backends ───────────────────────────────────────────────────────────────────

def _send_smtp(
    bcc_addresses: list[str],
    subject: str,
    plain: str,
    html: str | None,
    smtp_host: str,
    smtp_port: int,
    from_address: str,
    from_name: str,
    password: str,
) -> None:
    """Send an email via SMTP with TLS.

    Subscribers go in BCC so they cannot see each other's addresses.
    The To header is set to the sender address (a common convention for BCC-only sends).
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{from_name} <{from_address}>"
    msg['To'] = from_address  # visible To: sender — subscribers are hidden in BCC
    msg['Bcc'] = ', '.join(bcc_addresses)
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    if html:
        msg.attach(MIMEText(html, 'html', 'utf-8'))

    all_recipients = [from_address] + bcc_addresses
    logger.info("Sending email '%s' to %d BCC recipient(s)", subject, len(bcc_addresses))
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(from_address, password)
        server.sendmail(from_address, all_recipients, msg.as_string())
    logger.info("Email sent successfully")


def _send_file(
    to_addresses: list[str],
    subject: str,
    plain: str,
    html: str | None,
    output_dir: str,
) -> None:
    """Write the email to disk instead of sending it.

    Saves <output_dir>/email_<slug>.txt and <output_dir>/email_<slug>.html.
    Useful when no SMTP server is available (EMAIL_BACKEND=file).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    iso = now.isocalendar()
    slug = f"{now.year}-CW{iso.week:02d}"

    txt_path = Path(output_dir) / f"email_{slug}.txt"
    header = f"To: {', '.join(to_addresses)}\nSubject: {subject}\n\n"
    txt_path.write_text(header + plain, encoding='utf-8')
    logger.info("Email (plain) written to %s", txt_path)

    if html:
        html_path = Path(output_dir) / f"email_{slug}.html"
        html_path.write_text(html, encoding='utf-8')
        logger.info("Email (HTML) written to %s", html_path)


# ── Public send API ────────────────────────────────────────────────────────────

def send_digest(digest: dict, cfg: dict, smtp_password: str, admin_only: bool = False) -> None:
    """Deliver the weekly digest — via SMTP or file backend.

    admin_only: if True, send only to the admin address instead of the full subscriber list.
    """
    backend = cfg['email'].get('backend', 'smtp')
    admin_address = cfg['email']['admin_address']
    feed_link = cfg.get('feed', {}).get('link', '')
    subject, plain, html = render_digest_email(digest, feed_link=feed_link)

    if admin_only:
        bcc_addresses = [admin_address]
    else:
        subscribers = load_subscribers(cfg['email']['subscribers_file'])
        if not subscribers:
            logger.warning("No subscribers found — digest email not sent")
            return
        bcc_addresses = [s['email'] for s in subscribers]

    if backend == 'file':
        _send_file(
            to_addresses=bcc_addresses,
            subject=subject,
            plain=plain,
            html=html,
            output_dir=cfg['paths']['email_output_dir'],
        )
    else:
        _send_smtp(
            bcc_addresses=bcc_addresses,
            subject=subject,
            plain=plain,
            html=html,
            smtp_host=cfg['email']['smtp_host'],
            smtp_port=cfg['email']['smtp_port'],
            from_address=cfg['email']['from_address'],
            from_name=cfg['email']['from_name'],
            password=smtp_password,
        )
