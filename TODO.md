# SWE AI Digest — Improvement Ideas

A running list of ideas to explore. Not specs — just enough detail to know what
we had in mind when we start working on each one.

---

## ~~1. Hourly feed updates + weekly digest email~~ DONE

Implemented in the SQLite + CLI split refactor (`7b40a56`). `feed` runs hourly
(fetch → AI filter/summarize → RSS update), `digest` runs weekly (gather →
global summary → email). SQLite DB tracks state; NULL columns act as an implicit
state machine for crash recovery and incremental processing.

---

## 2. Proper transactional email service

**The idea:** Replace the personal Gmail account used for sending with a dedicated
transactional email provider.

**Why:** personal Gmail is fragile (App Password can break, rate limits, reputation risk),
not suitable long-term.

**Candidates:** Mailgun, Postmark, Resend, SendGrid — all have generous free tiers
for low-volume sending.

**Implementation:** The SMTP abstraction in `email_sender/sender.py` already isolates
this. For pure-SMTP providers it's just a config change (host, port, credentials).
For API-based providers (Resend, Postmark) a new backend would be needed alongside
the existing `smtp` and `file` backends.

**Things to sort out:**
- Choose a provider
- Set up a sending domain (SPF, DKIM, DMARC)
- Update `.env.example` and `config.yaml` with new defaults

---

## ~~3. Jinja2 templates instead of hardcoded HTML~~ DONE

Implemented in `0ccbb43`. All HTML generation uses Jinja2 templates in `templates/`:
`digest_page.html`, `index.html`, `email.html`, `email.txt`, `about.html`.

---

---

## 4. Expand and fix the engineer list

**The idea:** Two separate sub-tasks:

### 4a. Fix broken sources
13 sources are `type: chrome` and produce no content. Most are Twitter/X profiles.
Keep all engineers on the list — do not remove them. Instead, research whether each
has an alternative RSS-able presence.

Engineers still to research (currently Twitter/X only):
- DHH — has hey.com blog (already in list), check Bluesky/Mastodon
- Grady Booch
- Jonathan Blow
- Guido van Rossum
- Kevlin Henney
- Alan Kay
- Rich Hickey
- Jeff Dean
- Linus Torvalds
- Greg Young
- Michael Feathers
- Evan You

For each: check Substack (has RSS), personal blog, Mastodon, Bluesky (has RSS via
bridge), Medium, or any other platform with a feed. If no alternative found, leave
the chrome entry in place as a placeholder — do not delete.

- **Nitter** (Twitter RSS proxy) is largely dead. Not a viable fix.
- LinkedIn has no RSS — same dead end as Twitter.
- **Robert C. Martin (Uncle Bob)** — both sources marked `skip: true` (inactive since 2023).

### 4b. Add new engineers
Expand `data/digest_sources.yaml` with additional names.

Added so far:
- **Matteo Collina** (`c7dc80e`) — Node.js TSC, Fastify, Platformatic. Sources: Adventures in Nodeland newsletter + Bluesky.

Candidates still to research:
- Engineers with strong public writing on AI and software craft
- Prioritise those with RSS feeds to keep the pipeline working without scraping

---

## 5. Global / appearance-based sources

**The idea:** Beyond each engineer's own blog, scan a curated set of "platform"
sources for content *featuring* any engineer on the list — even if they didn't
publish it themselves.

**Examples:**
- **Podcasts:** Lex Fridman, drawkesh podcast, Pragmatic Engineer podcast, CoRecursive, Software
  Unscripted, The TWIML AI Podcast — guests are often on the engineer list
- **Conference talks:** YouTube channels of QCon, Strange Loop, GopherCon, GOTO,
  CraftConf — engineers give talks there that never appear on their blogs
- **Cross-platform appearances:** Substack posts by others quoting/featuring
  list members, HN threads where they comment (very noisy — probably skip)

**How it would work:**
- Maintain a separate `global_sources` section in `digest_sources.yaml` (or a
  new `global_sources.yaml`)
- Each global source has an RSS feed (podcast RSS, YouTube channel RSS)
- On fetch, check whether any engineer from the list is mentioned in the title
  or description — if yes, treat it as a candidate article for that engineer
- The AI then decides if the content is AI-relevant as usual

**Podcast source handling:**
- Use RSS episode descriptions as content — no transcript needed for a first version.
- Descriptions are typically short and won't dwell on AI details even when the episode
  is substantially about it. This means the current AI prompt (which expects article-level
  depth) will under-filter or miss relevant episodes.
- The prompt needs source-type-aware instructions: for podcast entries, the AI should
  be told it's evaluating an episode description, not a full article, and should apply
  a different relevance bar — if the guest is on the engineer list AND AI is a topic,
  that's sufficient signal.
- `digest_sources.yaml` already has a `type` field — we can add `type: podcast` and
  pass that metadata to the AI alongside the content.

**YouTube talk handling:**
- Pre-filter: include a video as a candidate if the engineer's name appears in the
  title **or** description. Both fields are available in YouTube RSS feeds.
- The AI then acts as a second gate: given the title + description, confirm the
  engineer actually gave the talk (vs. being briefly mentioned). Discard if not.
- This two-step approach keeps token cost low (pre-filter is cheap) while
  using the AI to resolve ambiguous matches.

**Open questions:**
- Attribution: credit as "featured in [Podcast Name]" rather than as the engineer's
  own content — needs a new field in the digest output (e.g. `source_type: podcast`,
  `source_name: Lex Fridman Podcast`).
- Do we include YouTube video descriptions the same way as podcast descriptions?
  Conference talk descriptions are often very thin ("Talk by X at QCon 2025").

---

## 6. Substack as a distribution platform

**The idea:** Publish the weekly digest to Substack in addition to (or instead of)
the current SMTP email + GitHub Pages setup.

**Why it's interesting:**
- Substack handles subscriber management, unsubscribes, and delivery reputation —
  removing the need for a dedicated transactional email service (see #2)
- Built-in discoverability: Substack has a network effect, readers can find the
  digest organically
- Substack publications have their own RSS feed — could replace or complement
  `docs/feed.xml`
- Free for publishers at low subscriber counts

**How it might work:**
- Substack has an unofficial API (used by their web app) but no official publishing API
- Alternatively: use email-to-Substack (Substack accepts posts via a dedicated inbox
  on some plans) — needs investigation
- Another approach: publish via Substack's web UI manually, using the pipeline to
  generate the content — loses full automation
- There are community tools (e.g. `substack-api` Python packages) but their
  reliability and ToS compliance are unknown

**Things to investigate:**
- Is there a supported/stable API for programmatic publishing?
- Substack is an **additional** channel, not a replacement. SMTP + GitHub Pages
  stay as-is. Add Substack as a new optional backend in `email_sender/sender.py`,
  enabled via config (`backend: substack` or a multi-backend list).
- Does moving to Substack change how we think about the GitHub Pages site — is it
  still needed, or does Substack's archive replace it?

---

## 8. Cross-source deduplication for the same author

**The problem:** Many engineers post on a primary source (e.g., their blog) and then
link to it from secondary sources (Mastodon, Bluesky, Twitter). The pipeline fetches
both and treats them as separate articles. This creates noise — the same content
appears twice (or more) under the same author, once as the full article and once as
a short social post linking to it.

**Examples:**
- Simon Willison publishes a blog post, then toots a link to it on Mastodon
- An engineer publishes on Substack, then posts the link on Bluesky

**Why current dedup doesn't catch it:** The DB deduplicates by exact URL. But the
blog post URL and the Mastodon/Bluesky post URL are different — the social post has
its own permalink, even though its content is just a link to the blog post.

**Possible strategies:**
- **URL extraction:** When processing a social media post, extract any URLs from the
  content. If a URL matches an article already in the DB from the same author, skip
  the social post.
- **AI-assisted dedup:** During the AI summarization step, pass the article's extracted
  URLs and let the model flag duplicates. More expensive but handles edge cases.
- **Source priority:** Assign priority to source types (blog > newsletter > mastodon >
  bluesky). When two articles from the same author share a URL in their content,
  keep only the highest-priority source's version.
- **Time-window grouping:** If the same author has two articles within a short window
  (e.g., 24h) where one's content is mostly a link to the other, merge them.

**Open questions:**
- Should the social post be dropped entirely, or kept but linked to the primary?
- What about cases where the social post adds meaningful commentary beyond just linking?

---

## ~~7. Bug fixes (shipped)~~ DONE

Bugs found and fixed during operations:

- **Scraped articles showing today's date in feed** (`fb37dc1`) — `Article.to_dict()` returned `published_at=None` for scrape sources (e.g. Paul Graham), causing the feed publisher to stamp them with `datetime.now()` on every rebuild. Fixed: fall back to `fetched_at`.
- **AI re-processing not-relevant articles on every run** (`599b212`) — `Article.unprocessed()` filtered on `summary IS NULL`, but not-relevant articles have `summary=None` and `ai_relevant=False`. They were re-sent to the AI every hourly run. Fixed: filter on `ai_relevant IS NULL` instead.
- **`skip: true` source attribute** (`20f2a80`) — replaced the old `type: skip` convention with an explicit `skip: true` attribute, preserving the real source type (rss, scrape, chrome) while disabling fetching.
- **Per-article summaries too long** (`25bfc2b`) — reduced prompt target from 100–150 words to 2–3 sentences (40–60 words).

<!-- Add new ideas below -->
