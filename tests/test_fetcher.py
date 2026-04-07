"""Tests for fetcher/core.py."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher.core import (
    build_engineer_index,
    dedup_by_url,
    entry_content_for_ai,
    entry_date,
    entry_summary,
    load_sources,
    parse_date,
    sort_and_filter_articles,
)


# ── parse_date ─────────────────────────────────────────────────────────────────

class TestParseDate:
    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert parse_date('') is None

    def test_iso_string(self):
        dt = parse_date('2026-04-01T10:00:00+00:00')
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_naive_string_gets_utc(self):
        dt = parse_date('2026-04-01T10:00:00')
        assert dt.tzinfo is not None

    def test_datetime_passthrough(self):
        original = datetime(2026, 4, 1, tzinfo=timezone.utc)
        assert parse_date(original) is original

    def test_naive_datetime_gets_utc(self):
        naive = datetime(2026, 4, 1)
        dt = parse_date(naive)
        assert dt.tzinfo is not None

    def test_invalid_string_returns_none(self):
        assert parse_date('not-a-date') is None


# ── entry_date ─────────────────────────────────────────────────────────────────

class TestEntryDate:
    def test_published_parsed(self):
        import time
        entry = MagicMock()
        entry.published_parsed = time.gmtime(0)
        entry.updated_parsed = None
        entry.created_parsed = None
        dt = entry_date(entry)
        assert dt is not None

    def test_falls_back_to_string_field(self):
        entry = MagicMock(spec=[])
        entry.published_parsed = None
        entry.updated_parsed = None
        entry.created_parsed = None
        entry.published = '2026-04-01T10:00:00+00:00'
        entry.updated = None
        entry.created = None
        dt = entry_date(entry)
        assert dt is not None
        assert dt.year == 2026

    def test_no_date_returns_none(self):
        entry = MagicMock(spec=[])
        for f in ('published_parsed', 'updated_parsed', 'created_parsed',
                  'published', 'updated', 'created'):
            setattr(entry, f, None)
        assert entry_date(entry) is None


# ── entry_summary ──────────────────────────────────────────────────────────────

class TestEntrySummary:
    def test_plain_summary(self):
        entry = MagicMock(spec=['summary'])
        entry.summary = 'Hello world'
        assert entry_summary(entry) == 'Hello world'

    def test_html_stripped(self):
        entry = MagicMock(spec=['summary'])
        entry.summary = '<p>Hello <b>world</b></p>'
        assert entry_summary(entry) == 'Hello world'

    def test_truncated(self):
        entry = MagicMock(spec=['summary'])
        entry.summary = 'a' * 1000
        assert len(entry_summary(entry, max_chars=100)) == 100

    def test_prefers_content_over_summary(self):
        entry = MagicMock(spec=['content', 'summary'])
        entry.content = [{'value': 'Full content'}]
        entry.summary = 'Short excerpt'
        assert entry_summary(entry) == 'Full content'

    def test_no_content_returns_empty(self):
        entry = MagicMock(spec=[])
        assert entry_summary(entry) == ''


# ── entry_content_for_ai ───────────────────────────────────────────────────────

class TestEntryContentForAI:
    def test_word_truncation(self):
        entry = MagicMock(spec=['summary'])
        entry.summary = ' '.join(['word'] * 3000)
        result = entry_content_for_ai(entry, max_words=2000)
        assert result.endswith('[truncated]')
        words = result.replace(' [truncated]', '').split()
        assert len(words) == 2000

    def test_short_content_not_truncated(self):
        entry = MagicMock(spec=['summary'])
        entry.summary = 'Hello world'
        result = entry_content_for_ai(entry, max_words=2000)
        assert result == 'Hello world'
        assert '[truncated]' not in result


# ── sort_and_filter_articles ───────────────────────────────────────────────────

class TestSortAndFilter:
    def test_filters_undated_scrape(self):
        articles = [
            {'source_type': 'scrape', 'published': None, 'priority': 1,
             'title': 'A', 'url': 'http://a.com'},
            {'source_type': 'rss', 'published': '2026-04-01T00:00:00+00:00',
             'priority': 1, 'title': 'B', 'url': 'http://b.com'},
        ]
        result = sort_and_filter_articles(articles)
        assert len(result) == 1
        assert result[0]['title'] == 'B'

    def test_sorted_by_priority_then_date(self):
        articles = [
            {'source_type': 'rss', 'published': '2026-04-01T00:00:00+00:00',
             'priority': 2, 'title': 'B', 'url': 'http://b.com'},
            {'source_type': 'rss', 'published': '2026-04-03T00:00:00+00:00',
             'priority': 1, 'title': 'A', 'url': 'http://a.com'},
        ]
        result = sort_and_filter_articles(articles)
        assert result[0]['priority'] == 1


# ── dedup_by_url ───────────────────────────────────────────────────────────────

class TestDedupByUrl:
    def test_removes_duplicates(self):
        articles = [
            {'url': 'http://a.com', 'title': 'A1'},
            {'url': 'http://a.com', 'title': 'A2'},
            {'url': 'http://b.com', 'title': 'B'},
        ]
        result = dedup_by_url(articles)
        assert len(result) == 2
        assert result[0]['title'] == 'A1'

    def test_empty_url_kept_but_not_deduped(self):
        articles = [
            {'url': '', 'title': 'X'},
            {'url': '', 'title': 'Y'},
        ]
        result = dedup_by_url(articles)
        assert len(result) == 2


# ── load_sources ───────────────────────────────────────────────────────────────

class TestLoadSources:
    def test_loads_yaml(self, sources_yaml_path):
        config = load_sources(str(sources_yaml_path))
        assert 'engineers' in config
        assert len(config['engineers']) == 2

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_sources(str(tmp_path / 'nonexistent.yaml'))


# ── build_engineer_index ───────────────────────────────────────────────────────

class TestBuildEngineerIndex:
    def test_lowercase_keys(self, sources_config):
        index = build_engineer_index(sources_config)
        assert 'alice engineer' in index
        assert 'bob coder' in index

    def test_values_are_engineer_dicts(self, sources_config):
        index = build_engineer_index(sources_config)
        assert index['alice engineer']['priority'] == 1
