"""Shared fixtures for the SWE AI Digest test suite."""

import textwrap
from pathlib import Path

import pytest
import yaml


MINIMAL_SOURCES = {
    'engineers': [
        {
            'name': 'Alice Engineer',
            'priority': 1,
            'bio': 'A great engineer.',
            'sources': [
                {'url': 'https://alice.example.com/feed.xml', 'type': 'rss', 'label': 'blog'},
            ],
        },
        {
            'name': 'Bob Coder',
            'priority': 2,
            'bio': 'Another great engineer.',
            'sources': [
                {'url': 'https://bob.example.com/feed.xml', 'type': 'rss', 'label': 'blog'},
                {'url': 'https://twitter.com/bobcoder', 'type': 'chrome', 'label': 'Twitter'},
            ],
        },
    ]
}


@pytest.fixture
def sources_config():
    return MINIMAL_SOURCES


@pytest.fixture
def sources_yaml_path(tmp_path):
    p = tmp_path / 'sources.yaml'
    p.write_text(yaml.dump(MINIMAL_SOURCES))
    return p


@pytest.fixture
def sample_articles():
    return [
        {
            'engineer': 'Alice Engineer',
            'priority': 1,
            'bio': 'A great engineer.',
            'source_type': 'rss',
            'source_label': 'blog',
            'source_url': 'https://alice.example.com/feed.xml',
            'title': 'Using AI to write better code',
            'url': 'https://alice.example.com/ai-coding',
            'published': '2026-04-01T10:00:00+00:00',
            'summary': 'Alice shares her experience using AI coding tools.',
            'content': 'I have been using AI coding assistants for six months now...',
        },
        {
            'engineer': 'Bob Coder',
            'priority': 2,
            'bio': 'Another great engineer.',
            'source_type': 'rss',
            'source_label': 'blog',
            'source_url': 'https://bob.example.com/feed.xml',
            'title': 'My new database library',
            'url': 'https://bob.example.com/db-lib',
            'published': '2026-04-02T10:00:00+00:00',
            'summary': 'Bob released a new database library.',
            'content': 'Today I am releasing version 1.0 of my database library...',
        },
    ]


@pytest.fixture
def sample_digest():
    return {
        'generated_at': '2026-04-04T10:00:00+00:00',
        'period_start': '2026-03-28T10:00:00+00:00',
        'period_end': '2026-04-04T10:00:00+00:00',
        'global_summary': 'This week engineers discussed AI coding tools extensively.',
        'stats': {
            'articles_fetched': 10,
            'articles_ai_related': 1,
            'articles_dropped': 7,
            'blogs_unreachable': 0,
            'chrome_only_skipped': 1,
        },
        'articles': [
            {
                'author': 'Alice Engineer',
                'bio': 'A great engineer.',
                'title': 'Using AI to write better code',
                'url': 'https://alice.example.com/ai-coding',
                'published_at': '2026-04-01T10:00:00+00:00',
                'summary': 'Alice describes how she integrated AI coding assistants into her workflow.',
            }
        ],
    }
