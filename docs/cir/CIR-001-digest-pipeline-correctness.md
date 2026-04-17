# CIR-001: Digest Pipeline Correctness

**Date:** 2026-04-17

## Intent

Fix article window selection in digest generation so it uses publication date
rather than fetch date, and resolve a cascade of bugs that made historical
digest regeneration unreliable. Also establish the source curation rule that
prevents redundant sources from producing duplicate articles.

## Behavior

- **Given** a digest for CW15 (Apr 6–12), **when** `Article.for_digest` is
  queried, **then** it returns articles whose `published_at` falls within the
  calendar week boundaries — not articles fetched during that window.

- **Given** an article with no `published_at` (scraped content such as Paul
  Graham's site), **when** the digest period is queried, **then** `fetched_at`
  is used as a fallback so scraped articles are not silently excluded.

- **Given** an existing digest row created with `--no-email`, **when**
  `--force` is passed, **then** the old row and its `digest_articles` join
  table entries are deleted before the new digest is inserted.

- **Given** a specific past week, **when** the operator runs
  `digest --week YYYY-CWnn --force --no-email`, **then** the digest page is
  regenerated with the correct article set without sending an email.

- This change does NOT affect how articles are AI-evaluated or summarized.

- This change does NOT affect the `feed.xml` publication flow or the hourly
  `feed` cron command.

## Constraints

- Article selection MUST use `COALESCE(published_at, fetched_at)` — using
  `published_at` alone would silently exclude scraped articles that have no
  date in the source HTML.
- `--force` MUST locate the existing digest by `label`, not by
  `Digest.for_period()`, because `for_period` filters `emailed_at IS NOT NULL`
  and is therefore blind to `--no-email` digests.
- The `--week` argument MUST accept `YYYY-CWnn` format (ISO week notation)
  to stay consistent with existing digest labels and page filenames.
- The join table (`digest_articles`) MUST be cleared with a raw SQL DELETE
  before deleting the digest row; SQLAlchemy's ORM `.clear()` on a lazy
  relationship silently fails when the relationship is not yet loaded.

## Decisions

| Proposal | Status | Reason |
|----------|--------|--------|
| Filter `for_digest` by `COALESCE(published_at, fetched_at)` | Accepted | Correctly scopes digests to publication week; fallback preserves scraped articles |
| Filter by `published_at` only | Rejected | Silently excludes scraped articles (Paul Graham, Norvig) that have no `published_at` |
| Keep filtering by `fetched_at` | Rejected | Fetch date drifts from publication date; makes historical regeneration produce wrong article sets |
| Use `period_end` as the query upper bound | Accepted | Correct semantics; the `published_at` fix makes the previous `query_end = now()` workaround unnecessary |
| Use `now()` as query upper bound (original code) | Rejected | During regeneration on Apr 16 for CW15, `now()` pulled in everything fetched since Apr 6 — including CW16 articles |
| Look up existing digest by `label` in `--force` path | Accepted | Works regardless of `emailed_at` status |
| Look up via `Digest.for_period()` in `--force` path | Rejected | `for_period` requires `emailed_at IS NOT NULL`; `--no-email` digests are invisible to it, causing UNIQUE constraint on re-insert |
| Delete join table rows via ORM `.articles.clear()` | Rejected | SQLAlchemy does not issue the DELETE if the relationship was never loaded in this session; FK constraint aborts the digest delete |
| Delete join table rows via raw SQL `DELETE FROM digest_articles` | Accepted | Explicit and reliable regardless of ORM session state |
| Remove redundant RSS feeds (Simon Willison `atom/links`, Martin Fowler Mastodon) | Accepted | Both sources overlap fully with a more comprehensive feed already configured; keeping them causes duplicate articles with different URL fragments that bypass the UNIQUE constraint |
| Normalize URLs at fetch time to strip fragments | Rejected | Conflicts with the podcast dedup strategy which intentionally appends `#engineer=slug` fragments to satisfy the UNIQUE constraint for multi-engineer episodes |
| Dedup at AI/digest-selection time | Rejected | Wrong layer; adds complexity where a simpler config change solves the problem permanently |

## Notes

The CW14 digest had accumulated 12 articles published in CW15 due to the
`query_end = now()` bug — they were included when CW14 was generated because
"now" at that point was already into CW15. Those 12 associations were removed
directly from `digest_articles` before regenerating CW15. Future digests are
not affected because the `published_at` fix prevents the window from drifting.

Source curation guidelines (prefer comprehensive feeds, avoid announcement-only
social accounts) are documented inline in `data/digest_sources.yaml` where they
are visible at the point of use.
