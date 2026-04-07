# SWE AI Digest — Design Document

## Origin & Purpose

This project was born from a conversation between friends trying to make sense of AI's trajectory and its impact on software engineering and the labor market. Rather than relying on hype or questionable credentials, the question was: *what are the engineers we actually admire saying about this?*

The result is a curated list of 40–50 recognized software engineers — John Carmack, Linus Torvalds, DHH, Jonathan Blow, Martin Fowler, Simon Willison, Uncle Bob, and others — who share their thinking publicly. This project exists to track what they write about AI: how they use it, what they think it means for the profession, and where they think it's going.

The spirit is one of curiosity, respect, and learning. Every piece of content processed by this pipeline belongs to its original author. Credit is always given. This tool aggregates and summarizes; it does not reproduce or claim ownership.

---

## What It Does

An end-to-end Python pipeline that:

1. Reads a YAML file listing canonical software engineers and their blog/feed URLs
2. Fetches content published in the past N days (default: 7) via RSS or web scraping
3. Sends all fetched content in a single batch to the Anthropic API, which:
   - Filters to only AI-relevant articles
   - Summarizes each qualifying article
   - Writes a global summary of the week's themes
4. Stores the structured result as a dated JSON digest
5. Sends an HTML email to subscribers
6. Updates a public RSS feed (hosted on GitHub Pages)
7. Persists state to avoid reprocessing articles in future runs

---

## Architecture

```
swe-ai-digest/
├── config.yaml              # All non-secret configuration
├── .env                     # Secrets (gitignored)
├── requirements.txt
├── main.py                  # Pipeline entrypoint
├── fetcher/
│   ├── rss.py               # RSS feed fetcher
│   └── scraper.py           # BeautifulSoup scrapers for non-RSS blogs
├── ai/
│   └── digest.py            # Anthropic API call, prompt, structured output
├── email/
│   └── sender.py            # SMTP email sender
├── feed/
│   └── rss_publisher.py     # RSS feed generator + GitHub Pages push
├── state/
│   └── tracker.py           # State persistence (seen article URLs)
├── output/                  # Generated digest JSON files (committed)
├── logs/                    # Run logs (gitignored or committed TBD)
├── docs/                    # Design and reference documentation
└── tests/                   # Unit/integration tests
```

---

## Configuration

All parameters live in `config.yaml`. Secrets are injected via environment variables (`.env` file, gitignored).

### config.yaml (planned keys)
```yaml
pipeline:
  lookback_days: 7          # How far back to scan (configurable per run via CLI arg)
  max_article_words: 2000   # Truncation limit before sending to AI

anthropic:
  model: claude-sonnet-4-6

email:
  smtp_host: ...
  smtp_port: 587
  from_address: ...
  admin_address: ...        # Gets notified on errors or zero-article weeks
  subscribers_file: data/subscribers.yaml

feed:
  output_path: output/feed.xml
  github_repo: ...          # GitHub Pages repo for feed hosting
  publisher_name: ...
  publisher_email: ...

paths:
  blogs_yaml: data/blogs.yaml
  state_file: data/state.json
  output_dir: output/
  logs_dir: logs/
```

### Secrets (via .env)
```
ANTHROPIC_API_KEY=
SMTP_PASSWORD=
GITHUB_TOKEN=              # For pushing feed.xml to GitHub Pages
```

---

## Input: blogs.yaml

Provided by the user. Already contains:
- Author name
- Author bio (short)
- Feed type: `rss` or `scrape`
- URL: direct RSS feed URL or blog homepage (for scraping)

---

## Content Fetching

- **RSS**: Standard feedparser-based fetch. Filter entries by `published` date within the lookback window.
- **Scraping**: BeautifulSoup. Each blog that requires scraping has a dedicated parser (brought over from the existing SWE Digest repo).
- **Twitter/X**: Out of scope. Skipped.
- **Unreachable blogs**: Skipped, but logged to the run log and noted in the digest stats.

---

## AI Processing

### Single batch call to Anthropic

All articles for the week are passed in one API call. The model receives structured input per article:

```
Author: ...
Bio: ...
Title: ...
URL: ...
Published: ...
Content: ... (truncated to 2000 words)
```

### What counts as AI-relevant

The prompt encodes the following criteria. An article qualifies if it substantively covers:
- How the author personally uses AI tools in their workflow
- AI's impact on the software engineering profession or labor market
- AI reshaping how software is designed, written, reviewed, or deployed
- Patterns, prompting techniques, agent architectures, tool evaluations
- Opinions or predictions about AI's trajectory in the industry

Passing mentions of AI do not qualify. The model is instructed to apply editorial judgment.

### Prompt design
- **Tone**: Analytical and neutral
- **System role**: Curator of a weekly digest for software engineers
- **Per-article output**: Summary of ~100–150 words that explains the author's argument and why it matters — "why relevant" is woven into the summary, not a separate field
- **Global summary**: ~200 words identifying the week's main themes and trends
- **Dropped articles**: Count returned in stats
- **Output**: Structured JSON enforced via Anthropic's native structured output (response schema)

### Error handling
- On API failure: exponential backoff, max 3 retries, then abort run and notify admin

---

## Output: Digest JSON

Stored as `output/digest_YYYY-CWxx.json`.

```json
{
  "generated_at": "2026-04-04T10:00:00Z",
  "period_start": "2026-03-28T00:00:00Z",
  "period_end": "2026-04-04T00:00:00Z",
  "global_summary": "...",
  "stats": {
    "articles_scanned": 42,
    "articles_ai_related": 15,
    "articles_dropped": 27,
    "blogs_unreachable": 2
  },
  "articles": [
    {
      "author": "Martin Fowler",
      "title": "...",
      "url": "...",
      "published_at": "2026-04-01T00:00:00Z",
      "summary": "..."
    }
  ]
}
```

---

## Output: Email

- **Provider**: SMTP (MVP). Substack integration considered for future.
- **Format**: Minimal HTML (headings, bold, links — nothing decorative)
- **Subject**: `SWE AI Digest — CW14 2026`
- **Structure**:
  1. Global summary
  2. Per author (grouped): author name, bio, then list of their articles with title (linked), and AI-generated summary
- **Zero-article weeks**: No digest email sent. Admin notification email sent instead.
- **Subscriber list**: `data/subscribers.yaml`

---

## Output: RSS Feed

- **Hosted on**: GitHub Pages (same repo, `gh-pages` branch or `docs/` folder — TBD)
- **URL pattern**: `https://username.github.io/swe-ai-digest/feed.xml`
- **Format**: RSS 2.0
- **Content**: AI-filtered articles only
- **Growth**: Ever-growing, no rotation or deletion of past items
- **Item order**: Date-descending by original article publication date
- **Per item includes**: author name, publication date, original title, AI-generated summary (bio not included)
- **Pipeline step**: After generating the digest JSON, the pipeline appends new items to `feed.xml` and pushes to GitHub

---

## State Management

`data/state.json` — a set of article URLs that have already been processed. Prevents re-summarizing the same articles on the next weekly run. Updated at the end of each successful run.

---

## Logging

- Log file: `logs/run_YYYY-CWxx.log`
- Also mirrored to stdout
- Captures: blogs fetched, articles found, articles filtered, API call status, email send status, feed push status, any errors

---

## Scheduling

- Runs via cron on an Ubuntu VPS
- Suggested cron: `0 7 * * 1` (Monday 07:00)
- Lookback window defaults to 7 days, overridable via CLI: `python main.py --days 14`

---

## Testing

Reasonable unit/integration test coverage for core modules:
- Fetcher (RSS parsing, scraper output)
- AI module (prompt construction, JSON schema validation)
- Email renderer
- RSS feed generator
- State tracker

---

## Future Considerations

- Substack API integration for email delivery
- Per-author relevance scoring
- Web UI for browsing past digests
- Slack/Discord notification channel
