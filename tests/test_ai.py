"""Tests for ai/digest.py."""

from unittest.mock import MagicMock, patch

import pytest

from ai.digest import build_articles_text, enrich_ai_articles


class TestBuildArticlesText:
    def test_includes_author_and_title(self, sample_articles):
        text = build_articles_text(sample_articles)
        assert 'Alice Engineer' in text
        assert 'Using AI to write better code' in text

    def test_numbered_blocks(self, sample_articles):
        text = build_articles_text(sample_articles)
        assert '=== ARTICLE 1 ===' in text
        assert '=== ARTICLE 2 ===' in text

    def test_includes_content(self, sample_articles):
        text = build_articles_text(sample_articles)
        assert 'I have been using AI coding assistants' in text

    def test_falls_back_to_summary(self):
        articles = [{
            'engineer': 'Test Author',
            'title': 'Test Article',
            'url': 'http://test.com',
            'published': '2026-04-01',
            'content': '',
            'summary': 'A short summary',
        }]
        text = build_articles_text(articles)
        assert 'A short summary' in text

    def test_empty_list(self):
        assert build_articles_text([]) == ''


class TestEnrichAIArticles:
    def test_joins_on_url(self, sample_articles):
        ai_result = {
            'articles': [{'url': 'https://alice.example.com/ai-coding', 'summary': 'AI summary'}],
            'dropped_count': 1,
        }
        enriched = enrich_ai_articles(ai_result, sample_articles)
        assert len(enriched) == 1
        assert enriched[0]['author'] == 'Alice Engineer'
        assert enriched[0]['summary'] == 'AI summary'
        assert enriched[0]['title'] == 'Using AI to write better code'

    def test_unknown_url_skipped(self, sample_articles):
        ai_result = {
            'articles': [{'url': 'https://unknown.example.com/post', 'summary': 'x'}],
            'dropped_count': 0,
        }
        enriched = enrich_ai_articles(ai_result, sample_articles)
        assert len(enriched) == 0

    def test_preserves_priority_order(self, sample_articles):
        ai_result = {
            'articles': [
                {'url': 'https://bob.example.com/db-lib', 'summary': 'Bob summary'},
                {'url': 'https://alice.example.com/ai-coding', 'summary': 'Alice summary'},
            ],
            'dropped_count': 0,
        }
        enriched = enrich_ai_articles(ai_result, sample_articles)
        assert enriched[0]['author'] == 'Alice Engineer'  # priority 1
        assert enriched[1]['author'] == 'Bob Coder'        # priority 2

    def test_bio_included(self, sample_articles):
        ai_result = {
            'articles': [{'url': 'https://alice.example.com/ai-coding', 'summary': 'x'}],
            'dropped_count': 0,
        }
        enriched = enrich_ai_articles(ai_result, sample_articles)
        assert enriched[0]['bio'] == 'A great engineer.'
