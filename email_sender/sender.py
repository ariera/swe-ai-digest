"""SMTP email sender for the SWE AI Digest."""

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml

logger = logging.getLogger(__name__)


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


def render_digest_email(digest: dict) -> tuple[str, str, str]:
    """Render the digest as (subject, plain_text, html).

    Articles are grouped by author. Each author section shows the bio, then
    their AI-relevant articles with title, link, and summary.
    """
    now = datetime.now(tz=timezone.utc)
    week_label = _week_label(now)
    subject = f"SWE AI Digest — {week_label}"

    period_start = digest.get('period_start', '')
    period_end = digest.get('period_end', '')
    global_summary = digest.get('global_summary', '')
    articles = digest.get('articles', [])

    # Group articles by author, preserving order of first appearance
    authors_seen: list[str] = []
    by_author: dict[str, list[dict]] = {}
    for a in articles:
        author = a['author']
        if author not in by_author:
            by_author[author] = []
            authors_seen.append(author)
        by_author[author].append(a)

    # ── Plain text ─────────────────────────────────────────────────────────────
    lines = [
        f"SWE AI Digest — {week_label}",
        f"Period: {period_start[:10]} to {period_end[:10]}",
        '',
        '=' * 60,
        'THIS WEEK IN AI × SOFTWARE ENGINEERING',
        '=' * 60,
        '',
        global_summary,
        '',
    ]

    for author in authors_seen:
        author_articles = by_author[author]
        bio = author_articles[0].get('bio', '')
        lines += [
            '-' * 60,
            author.upper(),
        ]
        if bio:
            lines += [bio, '']
        for a in author_articles:
            pub = a.get('published_at', '')[:10] if a.get('published_at') else ''
            lines += [
                f"  {a['title']}",
                f"  {a['url']}" + (f"  ({pub})" if pub else ''),
                '',
                f"  {a['summary']}",
                '',
            ]

    plain = '\n'.join(lines)

    # ── HTML ───────────────────────────────────────────────────────────────────
    html_parts = [
        '<html><body style="font-family: Georgia, serif; max-width: 700px; margin: auto; color: #222;">',
        f'<h1 style="font-size: 1.4em;">SWE AI Digest — {week_label}</h1>',
        f'<p style="color: #666; font-size: 0.9em;">Period: {period_start[:10]} to {period_end[:10]}</p>',
        '<hr>',
        '<h2 style="font-size: 1.15em;">This Week in AI &times; Software Engineering</h2>',
        f'<p>{_escape(global_summary)}</p>',
        '<hr>',
    ]

    for author in authors_seen:
        author_articles = by_author[author]
        bio = author_articles[0].get('bio', '')
        html_parts.append(f'<h2 style="font-size: 1.1em; margin-top: 2em;">{_escape(author)}</h2>')
        if bio:
            html_parts.append(f'<p style="font-style: italic; color: #555; font-size: 0.9em;">{_escape(bio)}</p>')
        for a in author_articles:
            pub = a.get('published_at', '')[:10] if a.get('published_at') else ''
            pub_str = f' <span style="color:#888; font-size:0.85em;">({pub})</span>' if pub else ''
            html_parts += [
                f'<h3 style="font-size: 1em; margin-bottom: 0.2em;">'
                f'<a href="{_escape(a["url"])}" style="color: #1a0dab;">{_escape(a["title"])}</a>'
                f'{pub_str}</h3>',
                f'<p style="margin-top: 0.3em;">{_escape(a["summary"])}</p>',
            ]

    html_parts.append('</body></html>')
    html = '\n'.join(html_parts)

    return subject, plain, html


def render_admin_notification(reason: str, details: str = '') -> tuple[str, str]:
    """Render a plain admin notification email."""
    subject = "SWE AI Digest — run notification"
    body = f"SWE AI Digest pipeline notification\n\nReason: {reason}\n"
    if details:
        body += f"\nDetails:\n{details}\n"
    return subject, body


def _escape(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
    )


# ── Backends ───────────────────────────────────────────────────────────────────

def _send_smtp(
    to_addresses: list[str],
    subject: str,
    plain: str,
    html: str | None,
    smtp_host: str,
    smtp_port: int,
    from_address: str,
    from_name: str,
    password: str,
) -> None:
    """Send an email via SMTP with TLS."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{from_name} <{from_address}>"
    msg['To'] = ', '.join(to_addresses)
    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    if html:
        msg.attach(MIMEText(html, 'html', 'utf-8'))

    logger.info("Sending email '%s' to %d recipient(s)", subject, len(to_addresses))
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(from_address, password)
        server.sendmail(from_address, to_addresses, msg.as_string())
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
    from datetime import datetime, timezone
    from pathlib import Path

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

def send_digest(digest: dict, cfg: dict, smtp_password: str) -> None:
    """Deliver the weekly digest — via SMTP or file backend."""
    backend = cfg['email'].get('backend', 'smtp')
    subscribers = load_subscribers(cfg['email']['subscribers_file'])
    if not subscribers and backend == 'smtp':
        logger.warning("No subscribers found — digest email not sent")
        return
    subject, plain, html = render_digest_email(digest)

    if backend == 'file':
        to_addresses = [s['email'] for s in subscribers] if subscribers else ['(no subscribers)']
        _send_file(
            to_addresses=to_addresses,
            subject=subject,
            plain=plain,
            html=html,
            output_dir=cfg['paths']['email_output_dir'],
        )
    else:
        if not subscribers:
            logger.warning("No subscribers found — digest email not sent")
            return
        _send_smtp(
            to_addresses=[s['email'] for s in subscribers],
            subject=subject,
            plain=plain,
            html=html,
            smtp_host=cfg['email']['smtp_host'],
            smtp_port=cfg['email']['smtp_port'],
            from_address=cfg['email']['from_address'],
            from_name=cfg['email']['from_name'],
            password=smtp_password,
        )


def send_admin_notification(reason: str, cfg: dict, smtp_password: str, details: str = '') -> None:
    """Send a plain notification to the admin address."""
    backend = cfg['email'].get('backend', 'smtp')
    subject, plain = render_admin_notification(reason, details)

    if backend == 'file':
        _send_file(
            to_addresses=[cfg['email'].get('admin_address', 'admin')],
            subject=subject,
            plain=plain,
            html=None,
            output_dir=cfg['paths']['email_output_dir'],
        )
        return

    _send_smtp(
        to_addresses=[cfg['email']['admin_address']],
        subject=subject,
        plain=plain,
        html=None,
        smtp_host=cfg['email']['smtp_host'],
        smtp_port=cfg['email']['smtp_port'],
        from_address=cfg['email']['from_address'],
        from_name=cfg['email']['from_name'],
        password=smtp_password,
    )
