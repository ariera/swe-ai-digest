"""Anthropic API integration — evaluates articles for AI relevance and summarises them.

Two main functions:
- summarize_article(): evaluates a single article for relevance + summary
- generate_global_summary(): synthesizes summaries into a digest overview
"""

import logging
import time
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# ── Single-article AI call ────────────────────────────────────────────────────

ARTICLE_SYSTEM_PROMPT = """\
You are the curator of the SWE AI Digest, a newsletter for software engineers \
tracking how leading practitioners think about artificial intelligence.

Your task is to evaluate a single article and decide whether it is AI-relevant, \
then write a summary if it is.

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

ARTICLE_USER_PROMPT_TEMPLATE = """\
Evaluate the following article for AI relevance.

If AI-relevant, write a concise summary of 2–3 sentences (aim for 40–60 words) that:
1. Explains the author's main argument or finding
2. Situates why it matters to a software engineer following AI developments

Prefer brevity. Only exceed 3 sentences if the article covers multiple distinct points \
that cannot be fairly collapsed into one.

If NOT AI-relevant, set ai_relevant to false and leave summary null.

Call the submit_article_result tool with your assessment.

Author: {author}
Title: {title}
URL: {url}
Published: {published}
Content:
{content}\
"""

PODCAST_ARTICLE_USER_PROMPT_TEMPLATE = """\
Evaluate the following podcast episode description for AI relevance.

This is a podcast episode, not a written article. The "content" field is the episode \
description or show notes — typically shorter marketing copy.

Relevance bar: if a known software engineer is a guest AND the episode topic substantively \
touches on AI (tools, workflows, impact on engineering, models, agents, etc.), that is \
sufficient for AI relevance. The bar is lower than for written articles — a 15-minute \
podcast segment on AI counts.

If AI-relevant, write a concise summary of 1–2 sentences (25–40 words) that:
1. Names the engineer guest and the podcast
2. Briefly states the AI angle discussed

If NOT AI-relevant, set ai_relevant to false and leave summary null.

Call the submit_article_result tool with your assessment.

Author: {author}
Title: {title}
URL: {url}
Published: {published}
Source: Podcast episode from {source_label}
Content:
{content}\
"""

SUBMIT_ARTICLE_RESULT_TOOL: dict[str, Any] = {
    'name': 'submit_article_result',
    'description': 'Submit the evaluation result for a single article.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'url': {
                'type': 'string',
                'description': 'Exact URL from the input article — do not modify.',
            },
            'ai_relevant': {
                'type': 'boolean',
                'description': 'True if the article is AI-relevant, false otherwise.',
            },
            'summary': {
                'type': ['string', 'null'],
                'description': '2–3 sentence summary (40–60 words) if AI-relevant, null otherwise.',
            },
        },
        'required': ['url', 'ai_relevant', 'summary'],
    },
}


def summarize_article(
    article_dict: dict,
    model: str,
    max_tokens: int,
    max_retries: int,
    api_key: str,
    source_type: str | None = None,
) -> dict:
    """Evaluate a single article for AI relevance and summarize if relevant.

    Returns a dict with keys: url, ai_relevant, summary.
    Raises RuntimeError if all retries are exhausted.
    """
    client = anthropic.Anthropic(api_key=api_key)
    content = article_dict.get('content') or article_dict.get('raw_content') or '(no content available)'
    effective_source_type = source_type or article_dict.get('source_type')
    if effective_source_type == 'podcast':
        user_message = PODCAST_ARTICLE_USER_PROMPT_TEMPLATE.format(
            author=article_dict.get('author') or article_dict.get('engineer', 'Unknown'),
            title=article_dict['title'],
            url=article_dict['url'],
            published=article_dict.get('published_at') or article_dict.get('published', 'unknown'),
            source_label=article_dict.get('attribution') or article_dict.get('source_label', 'Unknown Podcast'),
            content=content,
        )
    else:
        user_message = ARTICLE_USER_PROMPT_TEMPLATE.format(
            author=article_dict.get('author') or article_dict.get('engineer', 'Unknown'),
            title=article_dict['title'],
            url=article_dict['url'],
            published=article_dict.get('published_at') or article_dict.get('published', 'unknown'),
            content=content,
        )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "summarize_article — attempt %d/%d — %s",
                attempt, max_retries, article_dict['url'],
            )
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=ARTICLE_SYSTEM_PROMPT,
                tools=[SUBMIT_ARTICLE_RESULT_TOOL],
                tool_choice={'type': 'tool', 'name': 'submit_article_result'},
                messages=[{'role': 'user', 'content': user_message}],
            )
            for block in response.content:
                if block.type == 'tool_use' and block.name == 'submit_article_result':
                    result = block.input
                    logger.info(
                        "Article %s: ai_relevant=%s",
                        result.get('url'), result.get('ai_relevant'),
                    )
                    return result
            raise RuntimeError("submit_article_result tool was not called in the response")

        except anthropic.RateLimitError as e:
            last_error = e
            wait = 30 * (2 ** (attempt - 1))
            logger.warning("Rate limit hit (attempt %d/%d) — retrying in %ds", attempt, max_retries, wait)
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            last_error = e
            wait = 30 * (2 ** (attempt - 1))
            logger.warning("API error %s (attempt %d/%d) — retrying in %ds", e.status_code, attempt, max_retries, wait)
            time.sleep(wait)
        except Exception as e:
            last_error = e
            wait = 30 * (2 ** (attempt - 1))
            logger.warning("Unexpected error (attempt %d/%d): %s — retrying in %ds", attempt, max_retries, e, wait)
            time.sleep(wait)

    raise RuntimeError(f"summarize_article failed after {max_retries} attempts: {last_error}")


# ── Global summary AI call (Phase 2) ─────────────────────────────────────────

GLOBAL_SUMMARY_SYSTEM_PROMPT = """\
You are the curator of the SWE AI Digest, a newsletter for software engineers \
tracking how leading practitioners think about artificial intelligence.

Your task is to write a global summary of 150–200 words identifying the main themes \
and trends across the AI-relevant articles provided. The summaries have already been \
written — you are synthesising them into a cohesive overview.

Tone: analytical and neutral. Do not editorialize beyond what the authors state.\
"""

GLOBAL_SUMMARY_USER_PROMPT_TEMPLATE = """\
Below are the AI-relevant articles from this digest period, with their summaries.

Write a global summary of 150–200 words identifying the main themes and trends.

Call the submit_global_summary tool with your result.

{articles_text}\
"""

SUBMIT_GLOBAL_SUMMARY_TOOL: dict[str, Any] = {
    'name': 'submit_global_summary',
    'description': 'Submit the global summary for the digest.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'global_summary': {
                'type': 'string',
                'description': '150–200 word summary of themes across all AI-relevant articles.',
            },
        },
        'required': ['global_summary'],
    },
}


def build_summary_text(articles: list[dict]) -> str:
    """Format already-summarized articles for the global summary prompt."""
    blocks = []
    for i, a in enumerate(articles, 1):
        block = (
            f"=== ARTICLE {i} ===\n"
            f"Author: {a.get('author', 'Unknown')}\n"
            f"Title: {a['title']}\n"
            f"Summary: {a['summary']}"
        )
        blocks.append(block)
    return '\n\n'.join(blocks)


def generate_global_summary(
    articles_with_summaries: list[dict],
    model: str,
    max_tokens: int,
    max_retries: int,
    api_key: str,
) -> str:
    """Generate a global summary from already-summarized articles.

    Returns the global summary string.
    Raises RuntimeError if all retries are exhausted.
    """
    client = anthropic.Anthropic(api_key=api_key)
    articles_text = build_summary_text(articles_with_summaries)
    user_message = GLOBAL_SUMMARY_USER_PROMPT_TEMPLATE.format(articles_text=articles_text)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("generate_global_summary — attempt %d/%d (%d articles)", attempt, max_retries, len(articles_with_summaries))
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=GLOBAL_SUMMARY_SYSTEM_PROMPT,
                tools=[SUBMIT_GLOBAL_SUMMARY_TOOL],
                tool_choice={'type': 'tool', 'name': 'submit_global_summary'},
                messages=[{'role': 'user', 'content': user_message}],
            )
            for block in response.content:
                if block.type == 'tool_use' and block.name == 'submit_global_summary':
                    summary = block.input['global_summary']
                    logger.info("Global summary generated (%d chars)", len(summary))
                    return summary
            raise RuntimeError("submit_global_summary tool was not called in the response")

        except anthropic.RateLimitError as e:
            last_error = e
            wait = 30 * (2 ** (attempt - 1))
            logger.warning("Rate limit hit (attempt %d/%d) — retrying in %ds", attempt, max_retries, wait)
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            last_error = e
            wait = 30 * (2 ** (attempt - 1))
            logger.warning("API error %s (attempt %d/%d) — retrying in %ds", e.status_code, attempt, max_retries, wait)
            time.sleep(wait)
        except Exception as e:
            last_error = e
            wait = 30 * (2 ** (attempt - 1))
            logger.warning("Unexpected error (attempt %d/%d): %s — retrying in %ds", attempt, max_retries, e, wait)
            time.sleep(wait)

    raise RuntimeError(f"generate_global_summary failed after {max_retries} attempts: {last_error}")
