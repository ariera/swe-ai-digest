# Agent Instructions

## Adding a new engineer to the digest

When asked to add a new engineer to `data/digest_sources.yaml`, follow these steps carefully.

### 1. Research their online presence

Use web search to find where the engineer publishes publicly. Look for:

- **Personal blog** (WordPress, Ghost, Hugo, Jekyll, etc.) — check for RSS/Atom feed
- **Substack** — all Substacks have RSS at `https://<name>.substack.com/feed`
- **Mastodon / Fediverse** — all profiles have RSS at `https://<instance>/@<user>.rss`
- **Bluesky** — RSS available via bridges like `https://bsky.app/profile/<handle>/rss`
- **Medium** — RSS at `https://medium.com/feed/@<username>`
- **YouTube channel** — RSS at `https://www.youtube.com/feeds/videos.xml?channel_id=<id>`
- **GitHub blog / Discussions** — some engineers post via GitHub, check for Atom feeds
- **Newsletter platforms** (Buttondown, Ghost, ConvertKit) — most expose RSS

**Prefer RSS feeds.** They are fast, parallel, and don't require a browser. Only use `type: scrape` if the site has no feed but the HTML is simple. Use `type: chrome` as a last resort (Twitter/X, LinkedIn — these require authenticated browser sessions and are unreliable).

### 2. Verify each source

For every candidate source URL:

1. **Fetch the feed/page** to confirm it exists and returns valid content (use `WebFetch`)
2. **Confirm it belongs to the right person** — not a namesake, fan account, or aggregator. Check that the bio, about page, or feed content clearly identifies the engineer.
3. **Check it's active** — look at the most recent post date. If the last post is older than ~12 months, the source is likely abandoned. Still add it (content may resume), but note this with a `note:` field like `"Last post: 2024-03. May be inactive."`
4. **Check for duplicate URLs** — search the existing YAML to make sure the URL isn't already listed under another engineer

### 3. Write the bio

Write a one-sentence bio that covers:
- What the engineer is best known for (projects, companies, books)
- Why their perspective on AI/software engineering matters

Keep it factual and concise. Match the tone of existing bios in the file. Quote it in the YAML.

### 4. Choose the slug

The `slug` is a stable, lowercase, hyphenated identifier derived from the engineer's name. Examples: `simon-willison`, `dhh`, `john-carmack`. It is used as the database sync key and must never change once set.

- Check that the slug doesn't already exist in the file
- Use the most commonly known form of their name

### 5. Choose the priority

Priority determines ordering in the digest. Lower number = higher priority.

- **1-6**: Top-tier — engineers with frequent, high-quality public writing on AI
- **7-10**: Strong voices — solid public writing, but less frequent or less AI-focused
- **11+**: Worth tracking — occasional writers or primarily known for non-AI work

Look at existing priorities to calibrate. When in doubt, start at 10 — it can be adjusted later.

### 6. Add the entry to the YAML

Insert the new engineer in the correct position (grouped by priority tier, as marked by comments in the file). Use this format:

```yaml
  - slug: firstname-lastname
    name: Full Name
    priority: N
    bio: "One sentence bio."
    sources:
      - url: https://example.com/feed.xml
        type: rss
        label: blog
      - url: https://mastodon.social/@handle.rss
        type: rss
        label: Mastodon
```

**Field notes:**
- `label` is a short human-readable description shown in logs and debug output (e.g. "blog", "Substack", "Mastodon", "YouTube", "Twitter/X")
- `note` (optional) is for internal context that doesn't appear in the digest (e.g. "Supplement; shorter takes here", "Last post: 2024-03. May be inactive.")
- If a source is Twitter/X or LinkedIn with no RSS alternative, use `type: chrome` — but understand these sources are currently non-functional without a browser agent

### 7. Verify the result

After editing the YAML:

1. Run `python main.py feed --dry-run --debug` to confirm the new sources are picked up and fetched without errors
2. Check the debug output for the new engineer's name and source URLs
3. If a feed URL fails, investigate — it may need a different path (e.g. `/feed/` vs `/feed.xml` vs `/atom.xml` vs `/rss/`)

### Example: adding a new engineer

Suppose you're asked to add "Jane Developer" to the list:

1. Search: `"Jane Developer" blog RSS site:janedeveloper.com OR site:substack.com OR site:medium.com`
2. Find her blog at `janedeveloper.com` with an Atom feed at `/atom.xml`, and a Mastodon at `hachyderm.io/@jane`
3. Fetch both feeds, confirm recent posts (last post: 2 weeks ago), confirm they're hers
4. Check no existing entry for `jane-developer` or her URLs
5. Add:

```yaml
  - slug: jane-developer
    name: Jane Developer
    priority: 8
    bio: "Staff engineer at Acme Corp and author of Reliable Systems, writing frequently about AI-assisted code review and testing."
    sources:
      - url: https://janedeveloper.com/atom.xml
        type: rss
        label: blog
      - url: https://hachyderm.io/@jane.rss
        type: rss
        label: Mastodon
```

6. Run `python main.py feed --dry-run --debug` and confirm both sources fetch cleanly.
