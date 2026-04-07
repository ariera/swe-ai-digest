"""Anthropic API integration — filters articles for AI relevance and summarises them.

Single batch call per run. The model receives all new articles, decides which are
AI-relevant, writes per-article summaries, and returns a global weekly summary.
Structured output is enforced via tool_use with a fixed schema.
"""

import logging
import time
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the curator of the SWE AI Digest, a weekly newsletter for software engineers \
tracking how leading practitioners think about artificial intelligence.

Your task is to analyse a batch of blog posts and articles published by recognised \
software engineers, filter for AI-relevant content, and produce structured summaries.

An article is AI-relevant if it substantively addresses one or more of:
- How the author personally uses AI tools in their workflow (editors, copilots, agents, etc.)
- AI's impact on the software engineering profession or the broader labour market
- How AI is reshaping software design, code review, testing, or deployment practices
- Patterns, prompting techniques, agent architectures, or tool evaluations
- Opinions or predictions about AI's trajectory in the industry

An article is NOT AI-relevant if it merely mentions AI in passing, or if its primary \
subject is something else (a new library release, a historical retrospective, personal \
news) with incidental AI references.

Tone: analytical and neutral. Summaries must be factual and precise — do not \
editorialize beyond what the author states. Never invent details not present in the \
content provided.\
"""

USER_PROMPT_TEMPLATE = """\
Below are {n} articles published in the past {days} day(s) by software engineers we track.

For each AI-relevant article write a summary of 100–150 words that:
1. Explains the author's main argument or finding
2. Situates why it matters to a software engineer following AI developments

Then write a global summary of 150–200 words identifying the main themes and trends \
across all AI-relevant articles this week.

Call the submit_digest tool with your results.

{articles_text}\
"""

# ── Tool schema ────────────────────────────────────────────────────────────────

SUBMIT_DIGEST_TOOL: dict[str, Any] = {
    'name': 'submit_digest',
    'description': (
        'Submit the structured weekly digest. Call this exactly once with all '
        'AI-relevant articles and the global summary.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'global_summary': {
                'type': 'string',
                'description': '150–200 word summary of the week\'s themes across all AI-relevant articles.',
            },
            'articles': {
                'type': 'array',
                'description': 'AI-relevant articles only. Omit articles that are not AI-relevant.',
                'items': {
                    'type': 'object',
                    'properties': {
                        'url': {
                            'type': 'string',
                            'description': 'Exact URL from the input article — do not modify.',
                        },
                        'summary': {
                            'type': 'string',
                            'description': '100–150 word summary explaining the argument and its relevance.',
                        },
                    },
                    'required': ['url', 'summary'],
                },
            },
            'dropped_count': {
                'type': 'integer',
                'description': 'Number of articles judged not AI-relevant and excluded.',
            },
        },
        'required': ['global_summary', 'articles', 'dropped_count'],
    },
}


# ── Article text builder ───────────────────────────────────────────────────────

def build_articles_text(articles: list[dict]) -> str:
    """Format articles as numbered blocks for the prompt."""
    blocks = []
    for i, a in enumerate(articles, 1):
        content = a.get('content') or a.get('summary') or '(no content available)'
        block = (
            f"=== ARTICLE {i} ===\n"
            f"Author: {a['engineer']}\n"
            f"Title: {a['title']}\n"
            f"URL: {a['url']}\n"
            f"Published: {a.get('published', 'unknown')}\n"
            f"Content:\n{content}"
        )
        blocks.append(block)
    return '\n\n'.join(blocks)


# ── API call with retry ────────────────────────────────────────────────────────

def call_anthropic(
    articles: list[dict],
    days: int,
    model: str,
    max_tokens: int,
    max_retries: int,
    api_key: str,
) -> dict:
    """Call the Anthropic API and return the structured digest result.

    Returns a dict with keys: global_summary, articles (url+summary), dropped_count.
    Raises RuntimeError if all retries are exhausted.
    """
    client = anthropic.Anthropic(api_key=api_key)
    articles_text = build_articles_text(articles)
    user_message = USER_PROMPT_TEMPLATE.format(
        n=len(articles),
        days=days,
        articles_text=articles_text,
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Anthropic API call — attempt %d/%d (%d articles)", attempt, max_retries, len(articles))
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                tools=[SUBMIT_DIGEST_TOOL],
                tool_choice={'type': 'tool', 'name': 'submit_digest'},
                messages=[{'role': 'user', 'content': user_message}],
            )
            for block in response.content:
                if block.type == 'tool_use' and block.name == 'submit_digest':
                    logger.info(
                        "AI result: %d AI-relevant, %d dropped",
                        len(block.input.get('articles', [])),
                        block.input.get('dropped_count', 0),
                    )
                    return block.input
            raise RuntimeError("submit_digest tool was not called in the response")

        except anthropic.RateLimitError as e:
            last_error = e
            wait = 2 ** attempt
            logger.warning("Rate limit hit (attempt %d/%d) — retrying in %ds", attempt, max_retries, wait)
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            last_error = e
            wait = 2 ** attempt
            logger.warning("API error %s (attempt %d/%d) — retrying in %ds", e.status_code, attempt, max_retries, wait)
            time.sleep(wait)
        except Exception as e:
            last_error = e
            wait = 2 ** attempt
            logger.warning("Unexpected error (attempt %d/%d): %s — retrying in %ds", attempt, max_retries, e, wait)
            time.sleep(wait)

    raise RuntimeError(f"Anthropic API failed after {max_retries} attempts: {last_error}")


# ── Result enrichment ──────────────────────────────────────────────────────────

def enrich_ai_articles(ai_result: dict, fetched_articles: list[dict]) -> list[dict]:
    """Join AI output (url + summary) with original fetch data (author, title, etc.).

    The AI returns only the URL and its generated summary. This function looks up
    the full article metadata from the fetched batch and merges them.
    """
    url_index = {a['url']: a for a in fetched_articles}
    enriched = []
    for ai_article in ai_result.get('articles', []):
        url = ai_article['url']
        original = url_index.get(url)
        if original is None:
            logger.warning("AI returned unknown URL (skipping): %s", url)
            continue
        enriched.append({
            'author': original['engineer'],
            'bio': original.get('bio', ''),
            'title': original['title'],
            'url': url,
            'published_at': original.get('published'),
            'summary': ai_article['summary'],
        })
    # Preserve priority order from the original fetch
    priority_map = {a['url']: a.get('priority', 99) for a in fetched_articles}
    enriched.sort(key=lambda a: priority_map.get(a['url'], 99))
    return enriched
