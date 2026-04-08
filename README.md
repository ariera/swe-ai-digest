# SWE AI Digest

A weekly, automatically generated digest of what leading software engineers are writing about artificial intelligence.

## Why this exists

This project grew out of a simple question between friends: *amid all the noise about AI, what are the engineers we actually admire saying?*

The list — DHH, Martin Fowler, Simon Willison, Gergely Orosz, Kent Beck, and ~30 others — represents people who have demonstrated, over careers, an unusual ability to cut through complexity and say something true. They write publicly, they share their thinking generously, and in this particular moment in the history of computing, what they have to say about AI feels worth paying attention to.

This tool does nothing more than aggregate and summarise their public writing. Every article belongs to its original author. Credit is always given. The digest is a lens, not a claim of ownership.

The spirit is curiosity: staying close to the people whose judgement we trust, as they work through the same questions the rest of us are working through.

---

## What it does

An end-to-end Python pipeline that runs weekly:

1. Reads `data/digest_sources.yaml` — 34 engineers with their RSS feeds and scrapers
2. Fetches all content published in the past N days (default: 7)
3. Sends the batch to the Anthropic API (`claude-sonnet-4-6`) which filters for AI-relevant content and writes per-article summaries
4. Saves a structured JSON digest to `output/`
5. Emails the digest to subscribers via SMTP
6. Appends new items to `docs/feed.xml` (RSS 2.0) and pushes to GitHub Pages

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
# Normal weekly run
python main.py

# Dry run (fetch but skip processing, email and feed push)
python main.py --dry-run

# Custom lookback window
python main.py --days 14
```

---

## Scheduling (cron on Ubuntu)

The recommended approach is to run the script hourly with `--scheduled`. The script checks `config.yaml` to decide if today is a run day (default: Monday) and if it's past the scheduled hour (default: 07:00 UTC). A success marker (`data/.last_success`) prevents re-runs on the same day. If the API is down at 7:00, the 8:00 run retries automatically.

Add to your crontab (`crontab -e`):

```cron
# Run hourly — the script decides whether to proceed
0 * * * * /path/to/swe-ai-digest/.venv/bin/python3 /path/to/swe-ai-digest/main.py --scheduled >> /path/to/swe-ai-digest/logs/cron.log 2>&1
```

Configure schedule in `config.yaml`:
```yaml
pipeline:
  schedule:
    days: [1]       # Mon=1
    hour: 7          # UTC
```

To run manually (ignores schedule, no marker):
```bash
python main.py           # full run
python main.py --dry-run  # no AI call, no email, no push
```

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
| `output/digest_YYYY-CWxx.json` | Structured digest JSON for each run |
| `docs/feed.xml` | Ever-growing RSS 2.0 feed (AI-filtered articles only) |
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
├── main.py                  # Pipeline entrypoint
├── config.yaml              # All non-secret configuration
├── .env.example             # Secret keys template
├── requirements.txt
├── fetcher/
│   └── core.py              # RSS + BeautifulSoup fetchers (async)
├── ai/
│   └── digest.py            # Anthropic API call, prompt, structured output
├── email_sender/
│   └── sender.py            # SMTP email sender
├── feed/
│   └── publisher.py         # RSS 2.0 generator + GitHub push
├── data/
│   └── digest_sources.yaml  # 34 engineers: names, bios, feed URLs
├── output/                  # Generated digest JSON files
├── DESIGN.md                # Full design document
├── docs/                    # GitHub Pages (feed, site)
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
