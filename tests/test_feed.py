"""Tests for feed/publisher.py."""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from feed.publisher import _build_feed_xml, _load_existing_items, update_feed


CHANNEL_CFG = {
    'title': 'SWE AI Digest',
    'link': 'https://example.github.io/swe-ai-digest/feed.xml',
    'description': 'Weekly AI digest.',
    'publisher_name': 'SWE AI Digest',
    'publisher_email': 'digest@example.com',
}


class TestBuildFeedXml:
    def test_valid_rss_structure(self):
        xml = _build_feed_xml(CHANNEL_CFG, [])
        root = ET.fromstring(xml.split('\n', 1)[1])  # strip XML declaration
        assert root.tag == 'rss'
        assert root.get('version') == '2.0'
        assert root.find('channel') is not None

    def test_includes_items(self):
        items = [{
            'title': 'Test Article',
            'url': 'https://example.com/post',
            'author': 'Test Author',
            'pub_date': 'Tue, 01 Apr 2026 10:00:00 +0000',
            'description': 'A summary.',
            'guid': 'https://example.com/post',
        }]
        xml = _build_feed_xml(CHANNEL_CFG, items)
        assert 'Test Article' in xml
        assert 'Test Author' in xml
        assert 'A summary.' in xml

    def test_channel_metadata(self):
        xml = _build_feed_xml(CHANNEL_CFG, [])
        assert 'SWE AI Digest' in xml
        assert 'Weekly AI digest.' in xml


class TestLoadExistingItems:
    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_existing_items(tmp_path / 'feed.xml') == []

    def test_loads_items(self, tmp_path):
        items = [{
            'title': 'Old Post',
            'url': 'https://example.com/old',
            'author': 'Author',
            'pub_date': 'Mon, 01 Jan 2026 00:00:00 +0000',
            'description': 'Old summary.',
            'guid': 'https://example.com/old',
        }]
        feed_path = tmp_path / 'feed.xml'
        feed_path.write_text(_build_feed_xml(CHANNEL_CFG, items))
        loaded = _load_existing_items(feed_path)
        assert len(loaded) == 1
        assert loaded[0]['title'] == 'Old Post'

    def test_corrupt_xml_returns_empty(self, tmp_path):
        feed_path = tmp_path / 'feed.xml'
        feed_path.write_text('<broken xml')
        assert _load_existing_items(feed_path) == []


class TestUpdateFeed:
    def test_creates_feed_file(self, tmp_path, sample_digest):
        feed_cfg = {
            **CHANNEL_CFG,
            'output_path': 'docs/feed.xml',
            'auto_push': False,
        }
        update_feed(sample_digest, feed_cfg, str(tmp_path))
        feed_path = tmp_path / 'docs' / 'feed.xml'
        assert feed_path.exists()

    def test_appends_new_items(self, tmp_path, sample_digest):
        feed_cfg = {
            **CHANNEL_CFG,
            'output_path': 'docs/feed.xml',
            'auto_push': False,
        }
        update_feed(sample_digest, feed_cfg, str(tmp_path))
        update_feed(sample_digest, feed_cfg, str(tmp_path))  # run twice
        feed_path = tmp_path / 'docs' / 'feed.xml'
        items = _load_existing_items(feed_path)
        # Second run should not add duplicates
        assert len(items) == 1

    def test_feed_contains_article_data(self, tmp_path, sample_digest):
        feed_cfg = {
            **CHANNEL_CFG,
            'output_path': 'docs/feed.xml',
            'auto_push': False,
        }
        update_feed(sample_digest, feed_cfg, str(tmp_path))
        feed_path = tmp_path / 'docs' / 'feed.xml'
        content = feed_path.read_text()
        assert 'Using AI to write better code' in content
        assert 'Alice Engineer' in content
