"""Tests for fetcher/core.py."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher.core import (
    build_engineer_index,
    entry_content_for_ai,
    entry_date,
    entry_summary,
    fetch_podcast,
    load_sources,
    match_engineers_in_text,
    parse_date,
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

    def test_aliases_are_indexed(self):
        config = {
            'engineers': [
                {
                    'slug': 'antirez',
                    'name': 'Salvatore Sanfilippo (antirez)',
                    'aliases': ['antirez'],
                    'priority': 1,
                },
            ]
        }
        index = build_engineer_index(config)
        assert 'antirez' in index
        assert index['antirez']['slug'] == 'antirez'

    def test_parenthetical_name_also_indexed_without_suffix(self):
        config = {
            'engineers': [
                {
                    'slug': 'uncle-bob',
                    'name': 'Robert C. Martin (Uncle Bob)',
                    'aliases': ['Uncle Bob'],
                    'priority': 1,
                },
            ]
        }
        index = build_engineer_index(config)
        # Full name with parens
        assert 'robert c. martin (uncle bob)' in index
        # Name stripped of parenthetical
        assert 'robert c. martin' in index
        # Explicit alias
        assert 'uncle bob' in index

    def test_multiple_aliases(self):
        config = {
            'engineers': [
                {
                    'slug': 'dhh',
                    'name': 'DHH',
                    'aliases': ['David Heinemeier Hansson', 'DHH'],
                    'priority': 1,
                },
            ]
        }
        index = build_engineer_index(config)
        assert 'david heinemeier hansson' in index
        assert 'dhh' in index

    def test_alias_collision_keeps_first(self):
        """When two engineers share an alias, a warning is logged and the first wins."""
        config = {
            'engineers': [
                {'slug': 'eng1', 'name': 'Engineer One', 'aliases': ['Alias'], 'priority': 1},
                {'slug': 'eng2', 'name': 'Engineer Two', 'aliases': ['Alias'], 'priority': 2},
            ]
        }
        # Should not raise — collision is only logged as a warning, not an exception
        index = build_engineer_index(config)
        assert index['alias']['slug'] == 'eng1'


# ── match_engineers_in_text ───────────────────────────────────────────────────

_MATCH_CONFIG = {
    'engineers': [
        {
            'slug': 'alice',
            'name': 'Alice Engineer',
            'priority': 1,
        },
        {
            'slug': 'bob',
            'name': 'Bob Coder',
            'aliases': ['Robert Coder'],
            'priority': 2,
        },
        {
            'slug': 'antirez',
            'name': 'Salvatore Sanfilippo (antirez)',
            'aliases': ['antirez'],
            'priority': 3,
        },
    ]
}


class TestMatchEngineersInText:
    @pytest.fixture(autouse=True)
    def index(self):
        self.idx = build_engineer_index(_MATCH_CONFIG)

    def test_exact_match(self):
        matches = match_engineers_in_text('Alice Engineer joins the show', self.idx)
        assert len(matches) == 1
        assert matches[0]['slug'] == 'alice'

    def test_case_insensitive(self):
        matches = match_engineers_in_text('alice engineer discusses AI', self.idx)
        assert len(matches) == 1
        assert matches[0]['slug'] == 'alice'

    def test_alias_match(self):
        matches = match_engineers_in_text('Robert Coder talks about testing', self.idx)
        assert len(matches) == 1
        assert matches[0]['slug'] == 'bob'

    def test_parenthetical_alias(self):
        """'antirez' alias matches Salvatore Sanfilippo."""
        matches = match_engineers_in_text('antirez on Redis and AI', self.idx)
        assert len(matches) == 1
        assert matches[0]['slug'] == 'antirez'

    def test_no_match(self):
        matches = match_engineers_in_text('A completely random episode about Python', self.idx)
        assert matches == []

    def test_multi_match_deduped(self):
        """Two engineers mentioned → two entries, each appearing once."""
        matches = match_engineers_in_text('Alice Engineer and Bob Coder debate AI tools', self.idx)
        slugs = [m['slug'] for m in matches]
        assert 'alice' in slugs
        assert 'bob' in slugs
        assert len(slugs) == 2

    def test_same_engineer_via_two_aliases_deduped(self):
        """Matching the same engineer by both name and alias yields only one entry."""
        matches = match_engineers_in_text(
            'Salvatore Sanfilippo (antirez) joins the podcast', self.idx
        )
        slugs = [m['slug'] for m in matches]
        assert slugs.count('antirez') == 1


# ── fetch_podcast ─────────────────────────────────────────────────────────────

FAKE_PODCAST_RSS = '''\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Podcast</title>
    <item>
      <title>Alice Engineer on AI coding</title>
      <link>https://podcast.example.com/ep1</link>
      <pubDate>Mon, 07 Apr 2026 10:00:00 +0000</pubDate>
      <description>Alice Engineer discusses how she uses AI in her workflow.</description>
    </item>
    <item>
      <title>Bob Coder and Alice Engineer panel</title>
      <link>https://podcast.example.com/ep2</link>
      <pubDate>Tue, 08 Apr 2026 10:00:00 +0000</pubDate>
      <description>Bob Coder and Alice Engineer compare notes on AI tools.</description>
    </item>
    <item>
      <title>A generic Ruby episode</title>
      <link>https://podcast.example.com/ep3</link>
      <pubDate>Wed, 09 Apr 2026 10:00:00 +0000</pubDate>
      <description>No known engineers mentioned here.</description>
    </item>
  </channel>
</rss>
'''


class TestFetchPodcast:
    def _make_client(self):
        """Return a mock httpx.AsyncClient with FAKE_PODCAST_RSS content."""
        import httpx
        mock_resp = MagicMock()
        mock_resp.content = FAKE_PODCAST_RSS.encode('utf-8')
        mock_resp.raise_for_status = MagicMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=mock_resp)
        return client

    def test_matched_episode_returns_article(self):
        source = {'url': 'https://podcast.example.com/feed', 'label': 'Test Podcast'}
        engineer_index = build_engineer_index(_MATCH_CONFIG)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)

        articles, error = asyncio.run(
            fetch_podcast(self._make_client(), source, engineer_index, cutoff)
        )
        assert error is None
        alice_articles = [a for a in articles if a['slug'] == 'alice']
        assert len(alice_articles) >= 1

    def test_url_fragment_dedup_for_multi_engineer_episode(self):
        """When an episode matches two engineers, each gets a unique URL via #engineer= fragment."""
        source = {'url': 'https://podcast.example.com/feed', 'label': 'Test Podcast'}
        engineer_index = build_engineer_index(_MATCH_CONFIG)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)

        articles, _ = asyncio.run(
            fetch_podcast(self._make_client(), source, engineer_index, cutoff)
        )
        ep2_articles = [a for a in articles if 'ep2' in a['url']]
        assert len(ep2_articles) == 2
        urls = {a['url'] for a in ep2_articles}
        assert len(urls) == 2  # each has a unique #engineer= fragment

    def test_unmatched_episode_skipped(self):
        """Episodes with no known engineers are silently dropped."""
        source = {'url': 'https://podcast.example.com/feed', 'label': 'Test Podcast'}
        engineer_index = build_engineer_index(_MATCH_CONFIG)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)

        articles, _ = asyncio.run(
            fetch_podcast(self._make_client(), source, engineer_index, cutoff)
        )
        ep3_articles = [a for a in articles if 'ep3' in a['url']]
        assert ep3_articles == []

    def test_source_type_is_podcast(self):
        source = {'url': 'https://podcast.example.com/feed', 'label': 'Test Podcast'}
        engineer_index = build_engineer_index(_MATCH_CONFIG)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)

        articles, _ = asyncio.run(
            fetch_podcast(self._make_client(), source, engineer_index, cutoff)
        )
        assert all(a['source_type'] == 'podcast' for a in articles)

    def test_timeout_returns_error(self):
        import httpx
        source = {'url': 'https://podcast.example.com/feed', 'label': 'Test Podcast'}
        engineer_index = build_engineer_index(_MATCH_CONFIG)
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)

        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        articles, error = asyncio.run(
            fetch_podcast(client, source, engineer_index, cutoff)
        )
        assert articles == []
        assert error is not None
        assert 'Timeout' in error
