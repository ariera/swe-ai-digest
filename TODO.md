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
14 sources are currently marked `type: chrome` and produce no content. Most are
Twitter/X profiles. Keep all engineers on the list — do not remove them. Instead,
research whether each has an alternative RSS-able presence.

Engineers to research (currently Twitter/X only):
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

### 4b. Add new engineers
Expand `data/digest_sources.yaml` with additional names. Candidates to research:
- Engineers with strong public writing on AI and software craft
- Prioritise those with RSS feeds to keep the pipeline working without scraping

---

## 5. Global / appearance-based sources

**The idea:** Beyond each engineer's own blog, scan a curated set of "platform"
sources for content *featuring* any engineer on the list — even if they didn't
publish it themselves.

**Examples:**
- **Podcasts:** Lex Fridman, Pragmatic Engineer podcast, CoRecursive, Software
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

<!-- Add new ideas below -->
