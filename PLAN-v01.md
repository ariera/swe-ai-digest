# Plan: SQLite + CLI Split (feed / digest subcommands)

## Context

The swe-ai-digest pipeline is currently a single monolithic `main.py` that does everything in one pass: fetch → AI filter/summarize → email → RSS feed → pages. This makes it impossible to update the feed incrementally (hourly) while sending the digest email only once per week (Monday). The pipeline is also stateless (date-based cutoff, file-based success markers), which makes crash recovery fragile and prevents tracking what's been processed.

This refactor introduces a SQLite database (via SQLAlchemy ORM) as the single source of truth, and splits the CLI into two subcommands: `feed` (hourly) and `digest` (weekly). NULLable columns in the DB serve as an implicit state machine — each pipeline step just fills in NULLs. Crash recovery is automatic: the next run picks up wherever the previous one left off.

`digest_sources.yaml` remains the human-editable master config (versioned in git). On each run, authors and sources are synced from YAML into the DB. The DB is the runtime copy.

---

## Database Schema

Five tables. SQLAlchemy 2.0 declarative style.

```sql
authors
  id          INTEGER PRIMARY KEY
  name        TEXT UNIQUE NOT NULL
  bio         TEXT
  priority    INTEGER             -- lower = higher priority, synced from YAML

sources
  id          INTEGER PRIMARY KEY
  url         TEXT UNIQUE NOT NULL
  author_id   INTEGER FK → authors.id  -- nullable: NULL = global source (podcast, conference)
  type        TEXT NOT NULL             -- rss, scrape, chrome, podcast, youtube
  label       TEXT
  enabled     BOOLEAN NOT NULL DEFAULT TRUE  -- replaces type=skip from YAML

articles
  id          INTEGER PRIMARY KEY
  url         TEXT UNIQUE NOT NULL
  author_id   INTEGER NOT NULL FK → authors.id
  source_id   INTEGER FK → sources.id  -- which source it came from
  title       TEXT NOT NULL
  published_at TEXT                     -- ISO 8601, nullable (scrape sources)
  fetched_at  TEXT NOT NULL             -- ISO 8601
  raw_content TEXT                      -- original content (for re-summarization)
  summary     TEXT                      -- AI-generated; NULL = unprocessed
  ai_relevant BOOLEAN                  -- NULL = unprocessed; TRUE/FALSE = AI decided
  feed_at     TEXT                      -- NULL = not yet in feed.xml

digests
  id              INTEGER PRIMARY KEY
  week            TEXT UNIQUE NOT NULL  -- e.g. '2026-CW15'
  period_start    TEXT NOT NULL
  period_end      TEXT NOT NULL
  global_summary  TEXT                  -- AI-generated weekly summary
  created_at      TEXT NOT NULL
  emailed_at      TEXT                  -- NULL = not yet emailed
  page_at         TEXT                  -- NULL = page not yet published

digest_articles
  digest_id   INTEGER FK → digests.id
  article_id  INTEGER FK → articles.id
  PRIMARY KEY (digest_id, article_id)
```

### Key queries derived from NULLs

- `WHERE summary IS NULL` → needs AI processing
- `WHERE ai_relevant = TRUE AND feed_at IS NULL` → needs to go into feed.xml
- `WHERE ai_relevant = TRUE AND id NOT IN (SELECT article_id FROM digest_articles)` → available for next digest
- `digests WHERE week = ? AND emailed_at IS NOT NULL` → already sent (replaces `--scheduled` marker file)

### YAML → DB sync

On each run, `digest_sources.yaml` is synced into the DB:
- **Authors**: upsert by name — insert if new, update bio/priority if changed
- **Sources**: upsert by URL — insert if new, update type/label/enabled if changed. Map `type: skip` → `enabled = FALSE`
- **Linking**: each source's `author_id` is set based on the engineer it's listed under in the YAML
- **Global sources** (future): sources with no parent engineer get `author_id = NULL`

---

## CLI Design

```
python main.py feed   [--dry-run] [--debug] [--config PATH]
python main.py digest [--dry-run] [--no-email] [--admin-only]
                      [--calendar-week | --days N]
                      [--scheduled] [--debug] [--config PATH]
```

### `feed` (hourly cron, no guard needed)

1. Load config, logging, dotenv, init DB
2. Sync authors + sources from `digest_sources.yaml` into DB
3. Fetch all enabled sources via `fetch_all()` (uses config lookback window; DB unique constraint handles dedup)
4. For each fetched article: `INSERT OR IGNORE` into articles with `author_id` and `source_id`
5. `SELECT WHERE summary IS NULL` → send each to AI individually for relevance + summary
6. `UPDATE` each article with summary + ai_relevant (on AI failure for one article: log, skip, next run retries)
7. `SELECT WHERE ai_relevant = TRUE AND feed_at IS NULL` → update `docs/feed.xml`
8. `UPDATE feed_at` for those articles
9. Git push `docs/` if `auto_push` is set

### `digest` (weekly cron, Monday)

1. Load config, logging, dotenv, init DB
2. Compute target week label (e.g. `2026-CW15`) and period bounds
3. **Scheduling guard** (`--scheduled`): `SELECT FROM digests WHERE week = ? AND emailed_at IS NOT NULL`. If found → skip. No file-based marker needed.
4. Run a feed pass (steps 2-8 above) to catch stragglers. **If the feed pass fails, log warning and continue** — digest uses whatever is already summarized.
5. `SELECT WHERE ai_relevant = TRUE AND not yet linked to any digest` for the target period
6. If no articles → log and exit
7. Call AI for **global summary only** (individual summaries already exist)
8. `INSERT` digest row, link articles via `digest_articles`
9. Build digest dict from DB → write digest page + update index (existing templates)
10. Send email (unless `--dry-run` or `--no-email`)
11. `UPDATE digest.emailed_at`, `UPDATE digest.page_at`
12. Git push `docs/` if `auto_push`

---

## AI Module Split

File: `ai/digest.py`

### `summarize_article(article, model, max_tokens, max_retries, api_key) → dict`
- Used by `feed` command, called once per article
- Evaluates AI relevance + writes summary if relevant
- Tool schema: `submit_article_result` with `{url, summary, ai_relevant: bool}`
- Returns `{url, summary, ai_relevant}`
- On failure: raise, caller logs warning and moves to next article (leaves `summary IS NULL`)

### `generate_global_summary(articles_with_summaries, model, max_tokens, max_retries, api_key) → str`
- Used by `digest` command
- Receives already-summarized articles (author + title + summary — no full content)
- Returns the 150-200 word global weekly summary
- Tool schema: `submit_global_summary` with `{global_summary: str}`

Keep existing `call_anthropic()` temporarily until Phase 4, then remove.

---

## Adapter Layer

New file: `db/queries.py`

### Sync functions
- `sync_sources_from_yaml(session, sources_config)` — upserts all authors + sources from YAML
- `get_enabled_sources(session) → list[Source]` — for fetcher

### Article lifecycle
- `insert_article_if_new(session, ...) → Article | None` — INSERT OR IGNORE by URL
- `get_unprocessed_articles(session) → list[Article]` — `WHERE summary IS NULL`
- `update_article_ai_result(session, article_id, summary, ai_relevant)`
- `get_unfed_articles(session) → list[Article]` — `WHERE ai_relevant=TRUE AND feed_at IS NULL`
- `mark_articles_fed(session, article_ids, timestamp)`

### Digest lifecycle
- `get_articles_for_digest(session, period_start, period_end) → list[Article]`
- `create_digest(session, week, period_start, period_end, global_summary, articles) → Digest`
- `get_digest_by_week(session, week) → Digest | None`

### Dict adapters (for templates/email/feed — zero changes needed downstream)
- `article_to_dict(article: Article) → dict` — produces `{author, bio, title, url, published_at, summary}`
- `digest_to_dict(digest: Digest) → dict` — produces `{global_summary, period_start, period_end, articles: [...]}`

---

## Implementation Phases

### Phase 1: Database foundation (new files, zero breakage)
- [ ] Add `sqlalchemy>=2.0.0` to `requirements.txt`
- [ ] Create `db/__init__.py` (empty)
- [ ] Create `db/models.py` (Author, Source, Article, Digest, digest_articles)
- [ ] Create `db/session.py` (get_engine, get_session_factory, init_db)
- [ ] Add `db_path: data/articles.db` to `config.yaml` paths section
- [ ] Add `data/articles.db` to `.gitignore`
- [ ] Create `tests/test_db.py` (model constraint tests with in-memory SQLite)
- [ ] Run tests → all existing tests still pass, new DB tests pass

### Phase 2: AI module split (add new functions, keep old)
- [ ] Add `summarize_article()` to `ai/digest.py` — single-article AI call with new prompt + tool schema
- [ ] Add `generate_global_summary()` to `ai/digest.py` — summary-only AI call
- [ ] Add `build_summary_text()` helper for formatting summarized articles for the global summary prompt
- [ ] Keep existing `call_anthropic()`, `enrich_ai_articles()`, `build_articles_text()` unchanged
- [ ] Add tests for new functions in `tests/test_ai.py`
- [ ] Run tests → all pass

### Phase 3: Query layer and adapters (new file, no breakage)
- [ ] Create `db/queries.py` with all functions listed in the Adapter Layer section above
- [ ] Add tests in `tests/test_db.py` for each query function
- [ ] Test `sync_sources_from_yaml` with sample YAML → verify authors + sources created/updated
- [ ] Test `article_to_dict` / `digest_to_dict` produce dicts compatible with existing templates
- [ ] Run tests → all pass

### Phase 4: Rewrite main.py (the breaking change)
- [ ] Replace `parse_args()` with argparse subparsers (`feed`, `digest`)
- [ ] Extract shared setup into helper: config, logging, dotenv, DB engine/session init
- [ ] Implement `cmd_feed(args)`:
  - sync_sources_from_yaml → fetch_all → insert articles → AI per article → update feed.xml
- [ ] Implement `cmd_digest(args)`:
  - run cmd_feed (wrapped in try/except) → gather articles → global summary → digest page → email
- [ ] Remove: `_should_run()`, `_already_succeeded_today()`, `_already_succeeded_this_week()`, `_write_success_marker()`, `MARKER_FILE`, `build_digest()`, `save_digest()`, `_mock_ai_result()`
- [ ] Replace `--scheduled` logic: check DB for existing digest with `emailed_at IS NOT NULL`
- [ ] Adapt dry-run: `feed --dry-run` fetches + inserts but skips AI; `digest --dry-run` skips email, uses placeholder global summary
- [ ] Run all tests, fix breakage

### Phase 5: Cleanup and tests
- [ ] Remove `dedup_by_url()` and `sort_and_filter_articles()` from `fetcher/core.py`
- [ ] Remove old `call_anthropic()` and `enrich_ai_articles()` from `ai/digest.py`
- [ ] Update `tests/conftest.py`: add DB fixtures (`db_engine`, `db_session`, `sample_author`, `sample_db_articles`)
- [ ] Update `tests/test_fetcher.py`: remove tests for removed functions
- [ ] Add `tests/test_pipeline.py`: integration tests for `cmd_feed` and `cmd_digest` with mocked fetcher + AI
- [ ] Run full test suite

### Phase 6: Polish
- [ ] Update `README.md` with new CLI usage, crontab examples
- [ ] Update `config.yaml` comments
- [ ] Clean up `.gitignore` (remove `data/.last_success` if desired)

---

## Files Changed

| File | Action | Notes |
|---|---|---|
| `db/__init__.py` | **NEW** | Empty |
| `db/models.py` | **NEW** | Author, Source, Article, Digest, digest_articles |
| `db/session.py` | **NEW** | Engine, session factory, init |
| `db/queries.py` | **NEW** | All queries, YAML sync, dict adapters |
| `main.py` | **REWRITE** | Subcommands, DB integration |
| `ai/digest.py` | **MODIFY** | Add `summarize_article()`, `generate_global_summary()`, remove old functions |
| `fetcher/core.py` | **MODIFY** | Remove `dedup_by_url()`, `sort_and_filter_articles()` |
| `requirements.txt` | **MODIFY** | Add `sqlalchemy>=2.0.0` |
| `config.yaml` | **MODIFY** | Add `db_path` under paths |
| `.gitignore` | **MODIFY** | Add `data/articles.db` |
| `tests/conftest.py` | **MODIFY** | Add DB fixtures |
| `tests/test_db.py` | **NEW** | Model + query tests |
| `tests/test_ai.py` | **MODIFY** | Tests for new AI functions |
| `tests/test_fetcher.py` | **MODIFY** | Remove tests for removed functions |
| `tests/test_pipeline.py` | **NEW** | Integration tests |
| `README.md` | **MODIFY** | New CLI docs |

**Unchanged:** `templates/`, `email_sender/sender.py`, `pages/builder.py`, `feed/publisher.py`, `data/digest_sources.yaml`, `docs/about.html`

---

## Verification

1. **Unit tests:** `pytest tests/ -v` — all pass
2. **DB smoke test:** `python -c "from db.session import get_engine, init_db; e = get_engine('data/articles.db'); init_db(e); print('OK')"` — creates DB with 5 tables
3. **YAML sync:** `python -c "..."` — verify authors + sources from YAML appear in DB
4. **Feed dry-run:** `python main.py feed --dry-run --debug` — fetches, inserts into DB, skips AI
5. **Feed full run:** `python main.py feed --debug` — fetches, AI summarizes per article, updates feed.xml
6. **Digest dry-run:** `python main.py digest --dry-run --debug --admin-only` — builds digest from DB, skips email
7. **Digest full run:** `python main.py digest --debug --admin-only` — full digest, email to admin only
8. **Idempotency:** run `feed` twice — second run finds no new articles or unsummarized articles
9. **Crash recovery:** kill `feed` mid-AI — next run picks up articles with `summary IS NULL`
10. **Inspect DB:** `sqlite3 data/articles.db ".tables"` then `"SELECT count(*) FROM articles WHERE ai_relevant = TRUE"`
