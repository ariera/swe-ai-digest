# SWE AI Digest

A weekly, automatically generated digest of what leading software engineers are writing about artificial intelligence.

## Why this exists

This project grew out of a simple question between friends: *amid all the noise about AI, what are the engineers we actually admire saying?*

The list — DHH, Martin Fowler, Simon Willison, Gergely Orosz, Kent Beck, and ~30 others — represents people who have demonstrated, over careers, an unusual ability to cut through complexity and say something true. They write publicly, they share their thinking generously, and in this particular moment in the history of computing, what they have to say about AI feels worth paying attention to.

This tool does nothing more than aggregate and summarise their public writing. Every article belongs to its original author. Credit is always given. The digest is a lens, not a claim of ownership.

The spirit is curiosity: staying close to the people whose judgement we trust, as they work through the same questions the rest of us are working through.

---

## What it does

A Python pipeline split into two commands — **`feed`** (runs hourly) and **`digest`** (runs weekly):

**`feed`** — incremental article processing:
1. Syncs `data/digest_sources.yaml` (34 engineers) into a local SQLite database
2. Fetches all content from enabled RSS feeds and scrapers
3. Deduplicates articles by URL (database unique constraint)
4. Sends each new article to the Anthropic API (`claude-sonnet-4-6`) for AI-relevance filtering and per-article summary
5. Updates `docs/feed.xml` (RSS 2.0) with newly relevant articles and pushes to GitHub Pages

**`digest`** — weekly email + digest page:
1. Runs a feed pass to catch any stragglers
2. Gathers all AI-relevant articles not yet included in a previous digest
3. Generates a global summary via the Anthropic API
4. Builds a digest page in `docs/` and updates the index
5. Emails the digest to subscribers via SMTP
6. Automatically guards against duplicate sends (skip if already emailed for this period)

**AI relevance criteria** — an article qualifies if it substantively covers:
- How the author uses AI tools in their own workflow
- AI's impact on the software engineering profession or labour market
- How AI is reshaping how software is designed, written, reviewed, or deployed
- Prompting patterns, agent architectures, tool evaluations
- Opinions or predictions about AI's trajectory in the industry

---

## Setup

### 1. Clone and create a virtualenv

```bash
git clone https://github.com/yourusername/swe-ai-digest.git
cd swe-ai-digest
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in:
#   ANTHROPIC_API_KEY
#   SMTP_PASSWORD
#   GITHUB_TOKEN  (only needed if auto_push: true in config.yaml)
```

### 3. Configure the pipeline

Edit `config.yaml`:
- Set your SMTP host, from/admin addresses
- Set your GitHub Pages feed URL under `feed.link`
- Adjust `lookback_days` if needed

### 4. Add subscribers

```bash
cp data/subscribers.yaml.example data/subscribers.yaml
# Edit data/subscribers.yaml and add subscriber emails
```

### 5. Run

```bash
# Fetch articles, AI-filter, update RSS feed
python main.py feed

# Generate and email the weekly digest
python main.py digest

# Dry run (fetch + report only — no DB writes, no AI, no email)
python main.py feed --dry-run
python main.py digest --dry-run

# Digest with custom lookback window (default: previous calendar week)
python main.py digest --days 14

# Send digest email to admin only (for testing)
python main.py digest --admin-only

# Skip email but run everything else (DB, AI, feed, pages)
python main.py digest --no-email

# Force re-create a digest even if one was already sent for this period
python main.py digest --force

# Debug logging
python main.py feed --debug
```

---

## Scheduling (cron on Ubuntu)

Two cron jobs: `feed` runs every hour to keep the RSS feed current; `digest` runs hourly on Mondays with a built-in guard that prevents duplicate sends.

```cron
# Feed: hourly — fetch, AI summarize, update RSS feed
0 * * * * /path/to/swe-ai-digest/.venv/bin/python3 /path/to/swe-ai-digest/main.py feed >> /path/to/swe-ai-digest/logs/cron.log 2>&1

# Digest: hourly on Mondays — first successful run sends the email, subsequent runs skip
0 * * * 1 /path/to/swe-ai-digest/.venv/bin/python3 /path/to/swe-ai-digest/main.py digest >> /path/to/swe-ai-digest/logs/cron.log 2>&1
```

The `digest` command automatically checks whether a digest has already been emailed for the current period. If it has, it exits cleanly. If the 7am run fails (API down), the 8am run retries. No manual intervention needed.

---

## GitHub Pages (RSS feed)

1. Push the repo to GitHub
2. Go to **Settings → Pages**
3. Set source to **Deploy from a branch**, branch `main`, folder `/docs`
4. Your feed will be available at `https://yourusername.github.io/swe-ai-digest/feed.xml`
5. Update `feed.link` in `config.yaml` with this URL

The pipeline commits and pushes `docs/feed.xml` automatically after each run (set `auto_push: false` in `config.yaml` to disable).

---

## Output

| Path | Description |
|------|-------------|
| `data/swe_ai_digest.db` | SQLite database — single source of truth for articles, digests, authors |
| `docs/feed.xml` | Ever-growing RSS 2.0 feed (AI-filtered articles only) |
| `docs/digests/` | Published digest pages (one per period) |
| `logs/run_YYYY-CWxx.log` | Per-run log file |

---

## Running tests

```bash
pytest tests/ -v
```

---

## Project structure

```
swe-ai-digest/
├── main.py                  # CLI entrypoint (feed + digest subcommands)
├── config.yaml              # All non-secret configuration
├── .env.example             # Secret keys template
├── requirements.txt
├── db/
│   ├── models.py            # SQLAlchemy ORM models (Author, Source, Article, Digest)
│   └── session.py           # Engine, session factory, DB init
├── fetcher/
│   └── core.py              # RSS + BeautifulSoup fetchers (async)
├── ai/
│   └── digest.py            # Anthropic API: per-article summarize + global summary
├── email_sender/
│   └── sender.py            # SMTP email sender
├── feed/
│   └── publisher.py         # RSS 2.0 generator + GitHub push
├── pages/
│   └── builder.py           # Digest page generator
├── data/
│   ├── digest_sources.yaml  # 34 engineers: slugs, names, bios, feed URLs
│   └── swe_ai_digest.db     # SQLite database (gitignored)
├── DESIGN.md                # Full design document
├── docs/                    # GitHub Pages (feed, digest pages, site)
├── logs/                    # Run logs
└── tests/                   # pytest suite
```

---

## Credits

All content summarised in this digest belongs to the original authors. This tool reads their public RSS feeds and uses AI to summarise what they write — it does not reproduce, republish, or claim ownership of any of their work. Links to original articles are always included.

---

## Disclaimers

**This codebase was written by an AI agent.** The pipeline, module structure, prompts, tests, and documentation were implemented by Claude (Anthropic) under human direction. The humans defined the goals, made the design decisions, and reviewed the output — but the code itself was generated by AI.

**Article summaries and author bios are AI-generated.** The per-article summaries in each digest are produced by Claude based on the original text. The author bios in `data/digest_sources.yaml` were also written by AI. Both are best-effort summaries and may contain inaccuracies. When in doubt, read the original.
