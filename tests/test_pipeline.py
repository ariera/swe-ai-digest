"""Integration tests for cmd_feed and cmd_digest with mocked fetcher + AI."""

import argparse
import os
from unittest.mock import patch

import pytest

from db.models import Article, Author, Digest, sync_sources_from_yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIXTURE_SOURCES = {
    'engineers': [
        {
            'slug': 'alice-engineer',
            'name': 'Alice Engineer',
            'priority': 1,
            'bio': 'A great engineer.',
            'sources': [
                {'url': 'https://alice.example.com/feed.xml', 'type': 'rss', 'label': 'blog'},
            ],
        },
    ]
}


def _make_config(db_engine):
    """Create a patched _setup that uses the in-memory DB engine."""
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=db_engine)

    def patched_setup(args):
        from main import load_config, setup_logging
        from dotenv import load_dotenv
        load_dotenv()
        cfg = load_config(args.config)
        setup_logging(cfg['paths']['logs_dir'], debug=args.debug)
        cfg['_engine'] = db_engine
        cfg['_session_factory'] = factory
        return cfg

    return factory, patched_setup


def _fetch_result():
    """A plausible fetch_all result tuple."""
    articles = [
        {
            'engineer': 'Alice Engineer',
            'priority': 1,
            'bio': 'A great engineer.',
            'source_type': 'rss',
            'source_label': 'blog',
            'source_url': 'https://alice.example.com/feed.xml',
            'title': 'AI in my workflow',
            'url': 'https://alice.example.com/ai-workflow',
            'published': '2026-04-01T10:00:00+00:00',
            'content': 'I have been using AI tools extensively...',
        },
    ]
    return articles, [], []


def _mock_summarize(article_dict, **kwargs):
    return {
        'url': article_dict['url'],
        'ai_relevant': True,
        'summary': f"Mock summary for {article_dict['title']}",
    }


def _mock_global_summary(articles, **kwargs):
    return "Mock global summary."


def _common_patches():
    """Return a list of patches shared across pipeline tests."""
    return [
        patch('main._setup'),
        patch('main.asyncio.run', return_value=_fetch_result()),
        patch('main.load_sources', return_value=FIXTURE_SOURCES),
        patch('main.summarize_article', side_effect=_mock_summarize),
        patch('main.generate_global_summary', side_effect=_mock_global_summary),
        patch('main.update_feed'),
        patch('main.update_pages'),
        patch('main.push_docs'),
    ]


@pytest.fixture
def feed_args():
    return argparse.Namespace(
        command='feed', dry_run=False, debug=True,
        config=os.path.join(REPO_ROOT, 'config.yaml'),
    )


@pytest.fixture
def digest_args():
    return argparse.Namespace(
        command='digest', dry_run=False, no_email=True, admin_only=False,
        force=False, calendar_week=False, days=14, debug=True,
        config=os.path.join(REPO_ROOT, 'config.yaml'),
    )


class TestCmdFeed:
    @patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'})
    def test_feed_inserts_and_summarizes(self, feed_args, db_engine):
        from main import cmd_feed

        factory, patched_setup = _make_config(db_engine)

        # Pre-sync so author exists for lookup
        session = factory()
        sync_sources_from_yaml(session, FIXTURE_SOURCES)
        session.close()

        with patch('main._setup', side_effect=patched_setup), \
             patch('main.asyncio.run', return_value=_fetch_result()), \
             patch('main.load_sources', return_value=FIXTURE_SOURCES), \
             patch('main.summarize_article', side_effect=_mock_summarize), \
             patch('main.update_feed'), \
             patch('main.push_docs'):
            result = cmd_feed(feed_args)

        assert result == 0
        session = factory()
        articles = session.query(Article).all()
        assert len(articles) == 1
        assert articles[0].ai_relevant is True
        assert articles[0].summary is not None
        session.close()

    def test_feed_dry_run_no_db_writes(self, feed_args, db_engine):
        from main import cmd_feed

        feed_args.dry_run = True
        factory, patched_setup = _make_config(db_engine)

        with patch('main._setup', side_effect=patched_setup), \
             patch('main.asyncio.run', return_value=_fetch_result()), \
             patch('main.load_sources', return_value=FIXTURE_SOURCES):
            result = cmd_feed(feed_args)

        assert result == 0
        session = factory()
        assert session.query(Author).count() == 0
        assert session.query(Article).count() == 0
        session.close()


class TestCmdDigest:
    def _run_digest(self, digest_args, db_engine, extra_patches=None):
        """Helper to run cmd_digest with all mocks applied."""
        from main import cmd_digest

        factory, patched_setup = _make_config(db_engine)

        with patch('main._setup', side_effect=patched_setup), \
             patch('main.asyncio.run', return_value=_fetch_result()), \
             patch('main.load_sources', return_value=FIXTURE_SOURCES), \
             patch('main.summarize_article', side_effect=_mock_summarize), \
             patch('main.generate_global_summary', side_effect=_mock_global_summary), \
             patch('main.update_feed'), \
             patch('main.update_pages'), \
             patch('main.push_docs'), \
             patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):
            result = cmd_digest(digest_args)

        return result, factory

    def test_digest_creates_digest(self, digest_args, db_engine):
        result, factory = self._run_digest(digest_args, db_engine)
        assert result == 0

        session = factory()
        digests = session.query(Digest).all()
        assert len(digests) == 1
        assert digests[0].global_summary == "Mock global summary."
        assert len(digests[0].articles) == 1
        session.close()

    def test_digest_already_sent_guard(self, digest_args, db_engine):
        # First run
        result1, factory = self._run_digest(digest_args, db_engine)
        assert result1 == 0

        # Mark as emailed
        session = factory()
        digest = session.query(Digest).first()
        assert digest is not None
        digest.emailed_at = '2026-04-07T08:00:00Z'
        session.commit()
        session.close()

        # Second run — should be skipped by guard
        result2, _ = self._run_digest(digest_args, db_engine)
        assert result2 == 0

        session = factory()
        assert session.query(Digest).count() == 1  # no second digest
        session.close()

    def test_digest_force_overrides_guard(self, digest_args, db_engine):
        # First run
        _, factory = self._run_digest(digest_args, db_engine)

        # Mark as emailed
        session = factory()
        digest = session.query(Digest).first()
        assert digest is not None
        digest.emailed_at = '2026-04-07T08:00:00Z'
        session.commit()
        session.close()

        # Second run with --force, no new articles → exits cleanly
        digest_args.force = True
        from main import cmd_digest
        factory2, patched_setup = _make_config(db_engine)

        with patch('main._setup', side_effect=patched_setup), \
             patch('main.asyncio.run', return_value=([], [], [])), \
             patch('main.load_sources', return_value=FIXTURE_SOURCES), \
             patch('main.summarize_article', side_effect=_mock_summarize), \
             patch('main.generate_global_summary', side_effect=_mock_global_summary), \
             patch('main.update_feed'), \
             patch('main.update_pages'), \
             patch('main.push_docs'), \
             patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):
            result = cmd_digest(digest_args)

        assert result == 0
