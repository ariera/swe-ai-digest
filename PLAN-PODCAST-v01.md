# Plan: Podcast Support (Global Sources) — v02

## Context

The pipeline currently only fetches from per-engineer sources (RSS, scrape). TODO item #5 calls for "global sources" — podcast feeds that aren't tied to a single engineer. Instead, each episode is matched to engineers by checking if their name appears in the title or description.

Starting with podcasts only. YouTube/conference talks can follow the same pattern later.

---

## Design Decisions

**Name matching:** Case-insensitive substring match using a curated alias list per engineer. Each engineer in the YAML gets an optional `aliases` field listing nicknames, alternate spellings, and well-known handles:

```yaml
- slug: antirez
  name: Salvatore Sanfilippo
  aliases: [antirez]
  ...
- slug: dhh
  name: DHH
  aliases: [David Heinemeier Hansson]
  ...
- slug: robert-c-martin
  name: Robert C. Martin (Uncle Bob)
  aliases: [Uncle Bob, Bob Martin]
```

`build_engineer_index()` indexes the canonical name plus all aliases. No partial/first-name matching — too many false positives.

**Multi-engineer episodes:** One episode can match multiple engineers (panel discussions). URL uniqueness problem: `Article.url` has a UNIQUE constraint, so one episode URL can't be inserted twice. Solution: append `#engineer=slug` fragment to the URL — browsers strip fragments when navigating, so the link still works.

**Unmatched episodes:** Silently skipped. No "Global Podcasts" section — only episodes mentioning a known engineer are included.

**Attribution:** Podcast articles get an `attribution` field (e.g., "Featured in Lex Fridman Podcast") displayed in templates under the article title. Regular articles have `attribution = NULL`.

**AI prompt:** Separate podcast-specific prompt with a lower relevance bar. Podcast descriptions are short marketing copy — if a known engineer guest is present AND AI is mentioned in the topic, that's sufficient. Shorter summary target (1–2 sentences).

**DB migration:** Two new nullable columns on `articles` table (`source_type`, `attribution`). Use **Alembic** for schema migrations — proper version tracking, rollback support, no data loss.

---

## Implementation Steps

### Step 0: Set up Alembic

- Add `alembic` to `requirements.txt`
- Run `alembic init alembic` to create the `alembic/` directory structure
- Configure `alembic/env.py` to use `db.models.Base.metadata` and the DB path from `config.yaml`
- Configure `alembic.ini` with the SQLite URL
- Generate initial migration: `alembic revision --autogenerate -m "initial schema"` — this captures the current schema as the baseline
- Stamp the existing DB: `alembic stamp head` — marks the current DB as up-to-date without running migrations

### Step 1: DB schema migration + model changes (`db/models.py`)

Create Alembic migration to add two nullable columns to `articles`:
```
alembic revision --autogenerate -m "add source_type and attribution to articles"
```

This generates a migration adding:
```python
source_type = Column(String)       # 'rss', 'scrape', 'podcast'
attribution = Column(String)       # "Featured in Lex Fridman Podcast" or NULL
```

Run `alembic upgrade head` to apply.

Update `Article.insert_if_new()` — add `source_type` and `attribution` params.

Update `Article.to_dict()` — include `source_type` and `attribution`.

Update `sync_sources_from_yaml()` — loop over `global_sources` list, upsert each as a `Source` with `author_id=None`.

Update `_setup()` in `main.py` — call `alembic upgrade head` programmatically on startup (or document that it must be run manually before deploying).

### Step 2: YAML config (`data/digest_sources.yaml`)

Add `aliases` to engineers that have well-known nicknames:
```yaml
- slug: antirez
  name: Salvatore Sanfilippo
  aliases: [antirez]
- slug: dhh
  name: DHH
  aliases: [David Heinemeier Hansson]
- slug: robert-c-martin
  name: Robert C. Martin (Uncle Bob)
  aliases: [Uncle Bob, Bob Martin]
# ... audit all 35 engineers for common aliases
```

Add `global_sources` section at the end:
```yaml
global_sources:
  - url: https://lexfridman.com/feed/podcast/
    type: podcast
    label: Lex Fridman Podcast
  - url: https://corecursive.libsyn.com/feed
    type: podcast
    label: CoRecursive
  - url: https://twimlai.com/feed
    type: podcast
    label: The TWIML AI Podcast
  # ... more podcasts (research actual RSS URLs for Dwarkesh, Software Unscripted)
```

Update YAML header comment to document `global_sources` and `aliases`.

### Step 3: Fetcher (`fetcher/core.py`)

**Enhance: `build_engineer_index(sources_config)`**
- Index canonical name (lowercase) → engineer dict (existing behavior)
- Also index each alias (lowercase) → same engineer dict
- Deduplicate: if two aliases collide, log a warning

**New: `match_engineers_in_text(text, engineer_index) → list[dict]`**
- Lowercase the text
- For each key in engineer_index, check if it appears as a substring
- Return deduplicated list of matched engineer dicts

**New: `fetch_podcast(client, source, engineer_index, cutoff, max_content_words) → (articles, error)`**
- Fetch and parse RSS feed (reuse feedparser logic from `fetch_rss`)
- For each episode newer than cutoff: match engineers in title+description
- For each matched engineer: yield article dict with `source_type='podcast'`, `source_label=source['label']`
- URL fragment: `episode_url#engineer=slug` for dedup

**Modify: `fetch_all()`**
- After the existing engineer loop, iterate `sources_config.get('global_sources', [])`
- Dispatch `type: podcast` to `fetch_podcast()`
- `task_meta` entry uses synthetic name for error reporting

### Step 4: AI prompt (`ai/digest.py`)

**New: `PODCAST_USER_PROMPT_TEMPLATE`**
- States this is a podcast episode description
- Lower relevance bar: engineer on the list + AI topic = sufficient
- Includes `Source: Podcast episode from {source_label}`
- Shorter summary target: 1–2 sentences

**Modify: `summarize_article()`**
- Add `source_type=None` parameter
- Select prompt template based on `source_type == 'podcast'`

### Step 5: cmd_feed integration (`main.py`)

**Article insertion loop:**
- Pass `source_type` and `attribution` to `Article.insert_if_new()`
- Build attribution: `f"Featured in {a['source_label']}"` when `source_type == 'podcast'`

**AI processing loop:**
- Pass `article.source_type` to `summarize_article()`

### Step 6: Templates + feed publisher

**`templates/digest_page.html`** — after the `<h3>` title, add:
```html
{% if article.attribution %}
<p class="meta" style="font-style: italic;">{{ article.attribution }}</p>
{% endif %}
```

**`templates/email.html`** — same, with inline styles.

**`templates/email.txt`** — add attribution line after title.

**`feed/publisher.py`** — prepend attribution to RSS item description when present.

### Step 7: Tests

- `test_fetcher.py`: tests for `match_engineers_in_text()` (exact, case-insensitive, alias, no match, multi-match), tests for enhanced `build_engineer_index()` with aliases
- `test_db.py`: tests for new `Article` fields in `insert_if_new()` and `to_dict()`
- `test_ai.py`: test that `source_type='podcast'` selects the right prompt template
- `test_pipeline.py`: integration test with a mocked podcast fetch

---

## Critical Files

| File | Action |
|------|--------|
| `requirements.txt` | Add `alembic` |
| `alembic/` | **NEW** — Alembic config + migrations directory |
| `alembic.ini` | **NEW** — Alembic config file |
| `db/models.py` | Add columns, update `insert_if_new`, `to_dict`, `sync_sources_from_yaml` |
| `db/session.py` | Remove hand-rolled migration; Alembic handles it |
| `fetcher/core.py` | New `match_engineers_in_text`, `fetch_podcast`, modify `fetch_all`, enhance `build_engineer_index` |
| `ai/digest.py` | New podcast prompt, add `source_type` param to `summarize_article` |
| `main.py` | Pass new fields in insertion + AI loops |
| `data/digest_sources.yaml` | Add `global_sources` section, add `aliases` to engineers |
| `templates/digest_page.html` | Attribution display |
| `templates/email.html` | Attribution display |
| `templates/email.txt` | Attribution display |
| `feed/publisher.py` | Attribution in RSS description |

---

## Verification

1. `alembic upgrade head` — migration applies cleanly on existing DB
2. `pytest tests/ -v` — all existing + new tests pass
3. `python main.py feed --dry-run --debug` — podcast feeds fetched, episodes matched to engineers in logs
4. `python main.py feed --debug` — podcast articles inserted into DB with correct `source_type` and `attribution`
5. Inspect DB: `SELECT title, source_type, attribution FROM articles WHERE source_type = 'podcast'`
6. Check `docs/feed.xml` — podcast articles appear with attribution in description
7. Check digest page — podcast articles appear under matched engineer with "Featured in..." line
