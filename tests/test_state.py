"""Tests for state/tracker.py."""

import json
from pathlib import Path

import pytest

from state.tracker import filter_new_articles, load_state, mark_processed, save_state


class TestLoadState:
    def test_missing_file_returns_empty(self, tmp_path):
        state = load_state(str(tmp_path / 'state.json'))
        assert state['processed_urls'] == []
        assert state['last_run'] is None

    def test_loads_existing(self, tmp_path):
        p = tmp_path / 'state.json'
        p.write_text(json.dumps({'processed_urls': ['http://a.com'], 'last_run': '2026-04-01'}))
        state = load_state(str(p))
        assert 'http://a.com' in state['processed_urls']

    def test_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / 'state.json'
        p.write_text('not json {{')
        state = load_state(str(p))
        assert state['processed_urls'] == []


class TestSaveState:
    def test_saves_and_reloads(self, tmp_path):
        p = tmp_path / 'state.json'
        state = {'processed_urls': ['http://a.com'], 'last_run': '2026-04-04'}
        save_state(str(p), state)
        reloaded = load_state(str(p))
        assert 'http://a.com' in reloaded['processed_urls']

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / 'nested' / 'dir' / 'state.json'
        save_state(str(p), {'processed_urls': [], 'last_run': None})
        assert p.exists()


class TestFilterNewArticles:
    def test_filters_seen(self):
        articles = [
            {'url': 'http://a.com'},
            {'url': 'http://b.com'},
        ]
        state = {'processed_urls': ['http://a.com']}
        result = filter_new_articles(articles, state)
        assert len(result) == 1
        assert result[0]['url'] == 'http://b.com'

    def test_all_new(self):
        articles = [{'url': 'http://a.com'}, {'url': 'http://b.com'}]
        state = {'processed_urls': []}
        assert len(filter_new_articles(articles, state)) == 2

    def test_empty_url_not_filtered(self):
        articles = [{'url': ''}]
        state = {'processed_urls': []}
        result = filter_new_articles(articles, state)
        assert len(result) == 0  # empty URL is excluded


class TestMarkProcessed:
    def test_adds_urls(self):
        state = {'processed_urls': [], 'last_run': None}
        articles = [{'url': 'http://a.com'}, {'url': 'http://b.com'}]
        updated = mark_processed(state, articles)
        assert 'http://a.com' in updated['processed_urls']
        assert 'http://b.com' in updated['processed_urls']
        assert updated['last_run'] is not None

    def test_no_duplicates(self):
        state = {'processed_urls': ['http://a.com'], 'last_run': None}
        articles = [{'url': 'http://a.com'}]
        updated = mark_processed(state, articles)
        assert updated['processed_urls'].count('http://a.com') == 1
