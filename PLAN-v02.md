# Plan v02: SQLite + CLI Split (feed / digest subcommands)

## Context

The swe-ai-digest pipeline is currently a single monolithic `main.py` that does everything in one pass: fetch → AI filter/summarize → email → RSS feed → pages. This makes it impossible to update the feed incrementally (hourly) while sending the digest email only once per week (Monday). The pipeline is also stateless (date-based cutoff, file-based success markers), which makes crash recovery fragile and prevents tracking what's been processed.

This refactor introduces a SQLite database (via SQLAlchemy ORM) as the single source of truth, and splits the CLI into two subcommands: `feed` (hourly) and `digest` (weekly). NULLable columns in the DB serve as an implicit state machine — each pipeline step just fills in NULLs. Crash recovery is automatic: the next run picks up wherever the previous one left off.

`digest_sources.yaml` remains the human-editable master config (versioned in git). Each engineer gets a stable `slug` field used as the sync key. On each run, authors and sources are synced from YAML into the DB.

---

## Database Schema

Five tables. SQLAlchemy 2.0 declarative style. DB file: `data/swe_ai_digest.db`.

```sql
authors
  id          INTEGER PRIMARY KEY
  slug        TEXT UNIQUE NOT NULL     -- stable ID from YAML, e.g. 'simon-willison'
  name        TEXT NOT NULL
  bio         TEXT
  priority    INTEGER                  -- lower = higher priority, synced from YAML

sources
  id          INTEGER PRIMARY KEY
  url         TEXT UNIQUE NOT NULL
  author_id   INTEGER FK → authors.id  -- nullable: NULL = global source (podcast, conference)
  type        TEXT NOT NULL             -- rss, scrape, chrome, podcast, youtube
  label       TEXT
  enabled     BOOLEAN NOT NULL DEFAULT TRUE
  last_fetched_at TEXT                  -- ISO 8601, tracks when source was last fetched
  last_error      TEXT                  -- last fetch error message, NULL = no error

articles
  id          INTEGER PRIMARY KEY
  url         TEXT UNIQUE NOT NULL
  author_id   INTEGER NOT NULL FK → authors.id
  source_id   INTEGER FK → sources.id
  title       TEXT NOT NULL
  published_at TEXT                     -- ISO 8601, nullable (scrape sources)
  fetched_at  TEXT NOT NULL             -- ISO 8601
  raw_content TEXT                      -- original content (for re-summarization)
  summary     TEXT                      -- AI-generated; NULL = unprocessed
  ai_relevant BOOLEAN                  -- NULL = unprocessed; TRUE/FALSE = AI decided
  feed_at     TEXT                      -- NULL = not yet in feed.xml

digests
  id              INTEGER PRIMARY KEY
  label           TEXT UNIQUE NOT NULL  -- e.g. '2026-CW15', 'special-edition', '2026-CW15-CW16'
  period_start    TEXT NOT NULL         -- ISO 8601
  period_end      TEXT NOT NULL         -- ISO 8601
  global_summary  TEXT                  -- AI-generated summary
  created_at      TEXT NOT NULL
  emailed_at      TEXT                  -- NULL = not yet emailed
  page_at         TEXT                  -- NULL = page not yet published

digest_articles
  digest_id   INTEGER FK → digests.id
  article_id  INTEGER FK → articles.id
  PRIMARY KEY (digest_id, article_id)
```

### Key queries derived from NULLs

- `articles WHERE summary IS NULL` → needs AI processing
- `articles WHERE ai_relevant = TRUE AND feed_at IS NULL` → needs to go into feed.xml
- `articles WHERE ai_relevant = TRUE AND id NOT IN (SELECT article_id FROM digest_articles)` → available for next digest
- `digests WHERE period_end >= ? AND emailed_at IS NOT NULL` → already sent for this period (replaces `--scheduled` marker file)

### YAML → DB sync

`digest_sources.yaml` gets a new `slug` field per engineer:

```yaml
engineers:
  - slug: simon-willison         # stable ID, used as DB sync key
    name: Simon Willison
    priority: 1
    bio: "Creator of Datasette..."
    sources:
      - url: https://simonwillison.net/atom/everything
        type: rss
        label: blog
```

Sync logic on each run:
- **Authors**: upsert by `slug` — insert if new, update name/bio/priority if changed
- **Sources**: upsert by `url` — insert if new, update type/label/enabled if changed. Map `type: skip` → `enabled = FALSE`
- **Linking**: each source's `author_id` is set from its parent engineer in the YAML
- **Global sources** (future): sources with no parent engineer get `author_id = NULL`

### Source health tracking

`sources.last_fetched_at` and `sources.last_error` are updated after each fetch attempt.
Before sending a digest, the `digest` command checks the fetch failure rate:
- Count sources with `last_error IS NOT NULL` in this run
- If >50% of enabled sources errored: log a prominent warning in the digest output
- Does not block the digest — but the warning is visible in logs

---

## CLI Design

```
python main.py feed   [--dry-run] [--debug] [--config PATH]
python main.py digest [--dry-run] [--no-email] [--admin-only]
                      [--calendar-week | --days N]
                      [--scheduled] [--debug] [--config PATH]
```

### `--dry-run` vs `--no-email`

- `--dry-run`: **read-only**. Fetches articles and reports what it would do but makes no changes — no DB writes, no AI calls, no email, no feed/page updates.
- `--no-email`: full pipeline run (DB writes, AI, feed, pages) but **skips only the email send**. Useful for updating the feed/pages without emailing subscribers.

### `feed` (hourly cron, no guard needed)

1. Load config, logging, dotenv, init DB
2. Sync authors + sources from `digest_sources.yaml` into DB
3. Fetch all enabled sources (uses config lookback window)
4. Update `sources.last_fetched_at` / `sources.last_error` for each source
5. For each fetched article: `INSERT OR IGNORE` into articles (URL unique constraint = dedup)
6. `SELECT WHERE summary IS NULL` → send each to AI individually for relevance + summary
7. `UPDATE` each article with summary + ai_relevant (on AI failure for one article: log, skip, next run retries)
8. `SELECT WHERE ai_relevant = TRUE AND feed_at IS NULL` → update `docs/feed.xml`
9. `UPDATE feed_at` for those articles
10. Git push `docs/` if `auto_push`

With `--dry-run`: steps 1-3 execute (fetch + report), steps 4-10 are skipped.

### `digest` (weekly cron, Monday)

1. Load config, logging, dotenv, init DB
2. Compute target label (e.g. `2026-CW15`) and period bounds
3. **Scheduling guard** (`--scheduled`): check if a digest exists that covers this period with `emailed_at IS NOT NULL`. If found → skip. No file-based marker needed.
4. Run a feed pass (steps 2-10 above) to catch stragglers. **If the feed pass fails, log warning and continue** — digest uses whatever is already summarized.
5. Check source health: count errored sources, log warning if failure rate is high
6. `SELECT WHERE ai_relevant = TRUE AND not yet in any digest` for the target period
7. If no articles → log and exit
8. Call AI for **global summary only** (individual summaries already exist in DB)
9. `INSERT` digest row, link articles via `digest_articles`
10. Build digest dict from DB → write digest page + update index
11. Send email (unless `--dry-run` or `--no-email`)
12. `UPDATE digest.emailed_at`, `UPDATE digest.page_at`
13. Git push `docs/` if `auto_push`

With `--dry-run`: runs feed in dry-run mode, reports what digest would contain, no DB/email/page writes.

---

## AI Module Split

File: `ai/digest.py`

### `summarize_article(article_dict, model, max_tokens, max_retries, api_key) → dict`
- Used by `feed` command, called once per article
- Evaluates AI relevance + writes summary if relevant
- Tool schema: `submit_article_result` with `{url, ai_relevant: bool, summary: str | null}`
- Returns `{url, summary, ai_relevant}`
- On failure: raise; caller logs warning and moves to next article (leaves `summary IS NULL` for retry)

### `generate_global_summary(articles_with_summaries, model, max_tokens, max_retries, api_key) → str`
- Used by `digest` command
- Receives already-summarized articles (author + title + summary — no full content)
- Returns the 150-200 word global summary
- Tool schema: `submit_global_summary` with `{global_summary: str}`

Keep existing `call_anthropic()` temporarily until Phase 4 is complete, then remove.

---

## Model Methods (replaces separate queries.py)

Query logic lives on the models as class methods and instance methods:

### Author
- `Author.upsert(session, slug, name, bio, priority) → Author`
- `author.to_dict() → {name, bio, priority}`

### Source
- `Source.upsert(session, url, author_id, type, label, enabled) → Source`
- `Source.enabled_sources(session) → list[Source]`
- `source.record_fetch(session, error=None)` — updates last_fetched_at + last_error
- `Source.fetch_health(session) → (total, errored)` — counts for health check

### Article
- `Article.insert_if_new(session, url, author_id, source_id, title, ...) → Article | None`
- `Article.unprocessed(session) → list[Article]` — `WHERE summary IS NULL`
- `Article.unfed(session) → list[Article]` — `WHERE ai_relevant=TRUE AND feed_at IS NULL`
- `Article.for_digest(session, period_start, period_end) → list[Article]` — ai_relevant, not yet in any digest, in period
- `article.mark_ai_result(session, summary, ai_relevant)`
- `article.mark_fed(session, timestamp)`
- `article.to_dict() → dict` — `{author, bio, title, url, published_at, summary}` matching template expectations

### Digest
- `Digest.for_period(session, period_start, period_end) → Digest | None` — find existing
- `Digest.create(session, label, period_start, period_end, global_summary, articles) → Digest`
- `digest.to_dict() → dict` — `{label, global_summary, period_start, period_end, articles: [...]}` matching template expectations

### Standalone
- `sync_sources_from_yaml(session, sources_config)` — upserts all authors + sources from YAML. This is a module-level function in `db/models.py` since it orchestrates across Author + Source.

---

## Implementation Phases

### Phase 1: Database foundation (new files, zero breakage)
- [ ] Add `sqlalchemy>=2.0.0` to `requirements.txt`, install
- [ ] Add `slug` field to each engineer in `data/digest_sources.yaml`
- [ ] Create `db/__init__.py` (empty)
- [ ] Create `db/models.py` — Author, Source, Article, Digest, digest_articles, sync_sources_from_yaml
- [ ] Create `db/session.py` — get_engine, get_session_factory, init_db
- [ ] Add `db_path: data/swe_ai_digest.db` to `config.yaml` paths section
- [ ] Add `data/swe_ai_digest.db` to `.gitignore`
- [ ] Create `tests/test_db.py` — model constraint tests + query method tests (in-memory SQLite)
- [ ] Run tests → all existing tests still pass, new DB tests pass

### Phase 2: AI module split (add new functions, keep old)
- [ ] Add `summarize_article()` to `ai/digest.py` — single-article AI call with new prompt + tool schema
- [ ] Add `generate_global_summary()` to `ai/digest.py` — summary-only AI call
- [ ] Add `build_summary_text()` helper for formatting summarized articles
- [ ] Keep existing `call_anthropic()`, `enrich_ai_articles()`, `build_articles_text()` unchanged
- [ ] Add tests for new functions in `tests/test_ai.py`
- [ ] Run tests → all pass

### Phase 3: Rewrite main.py (the breaking change)
- [ ] Replace `parse_args()` with argparse subparsers (`feed`, `digest`)
- [ ] Extract shared setup into helper: config, logging, dotenv, DB init
- [ ] Implement `cmd_feed(args)`:
  - sync YAML → fetch → insert articles → AI per article → update feed.xml → push
- [ ] Implement `cmd_digest(args)`:
  - feed pass (try/except) → check health → gather articles → global summary → pages → email → push
- [ ] Remove old code: `_should_run()`, `_already_succeeded_today/this_week()`, `_write_success_marker()`, `MARKER_FILE`, `build_digest()`, `save_digest()`, `_mock_ai_result()`
- [ ] `--scheduled` now checks DB: digest exists for period with `emailed_at IS NOT NULL`
- [ ] `--dry-run` is read-only: fetch + report, no DB writes / AI / email / feed
- [ ] Run all tests, fix breakage

### Phase 4: Cleanup and tests
- [ ] Remove `dedup_by_url()` and `sort_and_filter_articles()` from `fetcher/core.py`
- [ ] Remove old `call_anthropic()` and `enrich_ai_articles()` from `ai/digest.py`
- [ ] Update `tests/conftest.py`: add DB fixtures (`db_engine`, `db_session`)
- [ ] Update `tests/test_fetcher.py`: remove tests for removed functions
- [ ] Add `tests/test_pipeline.py`: integration tests for `cmd_feed` and `cmd_digest` with mocked fetcher + AI
- [ ] Run full test suite

### Phase 5: Polish
- [ ] Update `README.md` with new CLI usage, crontab examples
- [ ] Update `config.yaml` comments
- [ ] Clean up `.gitignore`

---

## Files Changed

| File | Action | Notes |
|---|---|---|
| `db/__init__.py` | **NEW** | Empty |
| `db/models.py` | **NEW** | All models with class methods, sync_sources_from_yaml |
| `db/session.py` | **NEW** | Engine, session factory, init |
| `main.py` | **REWRITE** | Subcommands, DB integration |
| `ai/digest.py` | **MODIFY** | Add `summarize_article()`, `generate_global_summary()`, remove old |
| `fetcher/core.py` | **MODIFY** | Remove `dedup_by_url()`, `sort_and_filter_articles()` |
| `data/digest_sources.yaml` | **MODIFY** | Add `slug` field to each engineer |
| `requirements.txt` | **MODIFY** | Add `sqlalchemy>=2.0.0` |
| `config.yaml` | **MODIFY** | Add `db_path` under paths |
| `.gitignore` | **MODIFY** | Add `data/swe_ai_digest.db` |
| `tests/conftest.py` | **MODIFY** | Add DB fixtures |
| `tests/test_db.py` | **NEW** | Model + query method tests |
| `tests/test_ai.py` | **MODIFY** | Tests for new AI functions |
| `tests/test_fetcher.py` | **MODIFY** | Remove tests for removed functions |
| `tests/test_pipeline.py` | **NEW** | Integration tests |
| `README.md` | **MODIFY** | New CLI docs |

**Unchanged:** `templates/`, `email_sender/sender.py`, `pages/builder.py`, `feed/publisher.py`, `docs/about.html`

---

## Verification

1. **Unit tests:** `pytest tests/ -v` — all pass
2. **DB smoke test:** `python -c "from db.session import get_engine, init_db; e = get_engine('data/swe_ai_digest.db'); init_db(e); print('OK')"` — creates DB with 5 tables
3. **YAML sync:** verify authors + sources from YAML appear in DB with correct slugs
4. **Feed dry-run:** `python main.py feed --dry-run --debug` — fetches, reports new articles, no DB writes
5. **Feed full run:** `python main.py feed --debug` — fetches, AI summarizes, updates feed.xml
6. **Digest dry-run:** `python main.py digest --dry-run --debug --admin-only` — reports what digest would contain
7. **Digest full run:** `python main.py digest --debug --admin-only` — full digest, email to admin only
8. **Idempotency:** run `feed` twice — second run finds no new articles or unsummarized articles
9. **Crash recovery:** kill `feed` mid-AI — next run picks up articles with `summary IS NULL`
10. **Health check:** disable a source, run feed, check `sources.last_error` is populated
11. **Inspect DB:** `sqlite3 data/swe_ai_digest.db ".tables"` then `"SELECT count(*) FROM articles WHERE ai_relevant = TRUE"`
