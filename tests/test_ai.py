"""Tests for ai/digest.py."""

from unittest.mock import MagicMock, patch

import pytest

from ai.digest import (
    ARTICLE_USER_PROMPT_TEMPLATE,
    PODCAST_ARTICLE_USER_PROMPT_TEMPLATE,
    build_summary_text,
    generate_global_summary,
    summarize_article,
)


class TestBuildSummaryText:
    def test_includes_author_and_title(self):
        articles = [
            {'author': 'Alice', 'title': 'AI post', 'summary': 'Great article about AI.'},
        ]
        text = build_summary_text(articles)
        assert 'Alice' in text
        assert 'AI post' in text
        assert 'Great article about AI.' in text

    def test_numbered_blocks(self):
        articles = [
            {'author': 'Alice', 'title': 'Post 1', 'summary': 'S1'},
            {'author': 'Bob', 'title': 'Post 2', 'summary': 'S2'},
        ]
        text = build_summary_text(articles)
        assert '=== ARTICLE 1 ===' in text
        assert '=== ARTICLE 2 ===' in text

    def test_empty_list(self):
        assert build_summary_text([]) == ''


def _mock_tool_response(tool_name, result):
    """Create a mock Anthropic response with a tool_use block."""
    block = MagicMock()
    block.type = 'tool_use'
    block.name = tool_name
    block.input = result
    response = MagicMock()
    response.content = [block]
    return response


class TestSummarizeArticle:
    def test_returns_relevant_result(self):
        article = {
            'author': 'Alice',
            'title': 'AI in coding',
            'url': 'https://alice.com/ai',
            'published_at': '2026-04-01',
            'content': 'I have been using AI tools...',
        }
        mock_response = _mock_tool_response('submit_article_result', {
            'url': 'https://alice.com/ai',
            'ai_relevant': True,
            'summary': 'Alice discusses AI tools.',
        })
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            result = summarize_article(article, model='test', max_tokens=1024, max_retries=1, api_key='k')
        assert result['ai_relevant'] is True
        assert result['summary'] == 'Alice discusses AI tools.'
        assert result['url'] == 'https://alice.com/ai'

    def test_returns_not_relevant(self):
        article = {
            'author': 'Bob',
            'title': 'New DB library',
            'url': 'https://bob.com/db',
            'content': 'Released a database library...',
        }
        mock_response = _mock_tool_response('submit_article_result', {
            'url': 'https://bob.com/db',
            'ai_relevant': False,
            'summary': None,
        })
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            result = summarize_article(article, model='test', max_tokens=1024, max_retries=1, api_key='k')
        assert result['ai_relevant'] is False
        assert result['summary'] is None

    def test_raises_after_retries(self):
        article = {'author': 'X', 'title': 'T', 'url': 'https://x.com', 'content': 'c'}
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            MockClient.return_value.messages.create.side_effect = Exception("API down")
            with patch('ai.digest.time.sleep'):
                with pytest.raises(RuntimeError, match="failed after 2 attempts"):
                    summarize_article(article, model='test', max_tokens=1024, max_retries=2, api_key='k')

    def test_podcast_source_type_uses_podcast_prompt(self):
        """When source_type='podcast', the podcast-specific prompt must be sent to the API."""
        article = {
            'author': 'Alice',
            'title': 'Alice on The AI Podcast',
            'url': 'https://podcast.com/ep1#engineer=alice',
            'published_at': '2026-04-01',
            'content': 'Alice discusses her AI workflow.',
            'source_type': 'podcast',
            'attribution': 'Featured in The AI Podcast',
        }
        mock_response = _mock_tool_response('submit_article_result', {
            'url': 'https://podcast.com/ep1#engineer=alice',
            'ai_relevant': True,
            'summary': 'Alice talks about AI on the podcast.',
        })
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            mock_create = MockClient.return_value.messages.create
            mock_create.return_value = mock_response
            summarize_article(article, model='test', max_tokens=1024, max_retries=1, api_key='k',
                              source_type='podcast')
            call_kwargs = mock_create.call_args
            user_message = call_kwargs[1]['messages'][0]['content']
        # The podcast prompt mentions "Podcast episode from" — the regular prompt does not
        assert 'Podcast episode from' in user_message

    def test_regular_source_type_uses_standard_prompt(self):
        """When source_type is None or 'rss', the standard article prompt is used."""
        article = {
            'author': 'Alice',
            'title': 'Regular Blog Post',
            'url': 'https://alice.com/blog',
            'content': 'AI tools review.',
        }
        mock_response = _mock_tool_response('submit_article_result', {
            'url': 'https://alice.com/blog',
            'ai_relevant': True,
            'summary': 'Summary.',
        })
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            mock_create = MockClient.return_value.messages.create
            mock_create.return_value = mock_response
            summarize_article(article, model='test', max_tokens=1024, max_retries=1, api_key='k')
            call_kwargs = mock_create.call_args
            user_message = call_kwargs[1]['messages'][0]['content']
        assert 'Podcast episode from' not in user_message


class TestGenerateGlobalSummary:
    def test_returns_summary(self):
        articles = [
            {'author': 'Alice', 'title': 'AI post', 'summary': 'Great article.'},
        ]
        mock_response = _mock_tool_response('submit_global_summary', {
            'global_summary': 'This week AI was a major theme.',
        })
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response
            result = generate_global_summary(articles, model='test', max_tokens=1024, max_retries=1, api_key='k')
        assert result == 'This week AI was a major theme.'

    def test_raises_after_retries(self):
        articles = [{'author': 'X', 'title': 'T', 'summary': 'S'}]
        with patch('ai.digest.anthropic.Anthropic') as MockClient:
            MockClient.return_value.messages.create.side_effect = Exception("API down")
            with patch('ai.digest.time.sleep'):
                with pytest.raises(RuntimeError, match="failed after 2 attempts"):
                    generate_global_summary(articles, model='test', max_tokens=1024, max_retries=2, api_key='k')
