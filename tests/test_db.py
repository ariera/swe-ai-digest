"""Tests for the database models and query methods."""

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import Article, Author, Digest, Source, sync_sources_from_yaml


class TestAuthor:
    def test_upsert_creates_new(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio="A dev", priority=1)
        assert author.id is not None
        assert author.slug == "alice"
        assert author.name == "Alice"

    def test_upsert_updates_existing(self, db_session):
        Author.upsert(db_session, slug="alice", name="Alice", bio="A dev", priority=1)
        author = Author.upsert(db_session, slug="alice", name="Alice Updated", bio="Senior dev", priority=2)
        assert author.name == "Alice Updated"
        assert author.priority == 2
        assert db_session.query(Author).count() == 1

    def test_slug_unique_constraint(self, db_session):
        db_session.add(Author(slug="alice", name="Alice"))
        db_session.flush()
        db_session.add(Author(slug="alice", name="Alice 2"))
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_to_dict(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio="A dev", priority=1)
        d = author.to_dict()
        assert d == {"name": "Alice", "bio": "A dev", "priority": 1}


class TestSource:
    def test_upsert_creates_new(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        source = Source.upsert(db_session, url="https://example.com/feed", author_id=author.id, type="rss", label="blog")
        assert source.id is not None
        assert source.enabled is True

    def test_upsert_updates_existing(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        Source.upsert(db_session, url="https://example.com/feed", author_id=author.id, type="rss", label="blog")
        source = Source.upsert(db_session, url="https://example.com/feed", author_id=author.id, type="rss", label="main blog")
        assert source.label == "main blog"
        assert db_session.query(Source).count() == 1

    def test_url_unique_constraint(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        db_session.add(Source(url="https://x.com/feed", author_id=author.id, type="rss"))
        db_session.flush()
        db_session.add(Source(url="https://x.com/feed", author_id=author.id, type="rss"))
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_enabled_sources(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        Source.upsert(db_session, url="https://a.com/feed", author_id=author.id, type="rss", label="a", enabled=True)
        Source.upsert(db_session, url="https://b.com/feed", author_id=author.id, type="skip", label="b", enabled=False)
        enabled = Source.enabled_sources(db_session)
        assert len(enabled) == 1
        assert enabled[0].url == "https://a.com/feed"

    def test_record_fetch_success(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        source = Source.upsert(db_session, url="https://a.com/feed", author_id=author.id, type="rss", label="a")
        source.record_fetch(db_session)
        assert source.last_fetched_at is not None
        assert source.last_error is None

    def test_record_fetch_error(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        source = Source.upsert(db_session, url="https://a.com/feed", author_id=author.id, type="rss", label="a")
        source.record_fetch(db_session, error="Connection timeout")
        assert source.last_fetched_at is not None
        assert source.last_error == "Connection timeout"

    def test_fetch_health(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio=None, priority=1)
        s1 = Source.upsert(db_session, url="https://a.com/feed", author_id=author.id, type="rss", label="a")
        s2 = Source.upsert(db_session, url="https://b.com/feed", author_id=author.id, type="rss", label="b")
        s1.record_fetch(db_session)
        s2.record_fetch(db_session, error="Timeout")
        total, errored = Source.fetch_health(db_session)
        assert total == 2
        assert errored == 1


class TestArticle:
    def _make_author_source(self, db_session):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio="A dev", priority=1)
        source = Source.upsert(db_session, url="https://a.com/feed", author_id=author.id, type="rss", label="blog")
        return author, source

    def test_insert_if_new_creates(self, db_session):
        author, source = self._make_author_source(db_session)
        article = Article.insert_if_new(
            db_session, url="https://a.com/post1", author_id=author.id, source_id=source.id,
            title="Post 1", published_at="2026-04-01T10:00:00Z", raw_content="Hello world",
        )
        assert article is not None
        assert article.id is not None
        assert article.summary is None
        assert article.ai_relevant is None

    def test_insert_if_new_returns_none_for_duplicate(self, db_session):
        author, source = self._make_author_source(db_session)
        Article.insert_if_new(db_session, url="https://a.com/post1", author_id=author.id, source_id=source.id, title="Post 1")
        dup = Article.insert_if_new(db_session, url="https://a.com/post1", author_id=author.id, source_id=source.id, title="Post 1 again")
        assert dup is None
        assert db_session.query(Article).count() == 1

    def test_unprocessed(self, db_session):
        author, source = self._make_author_source(db_session)
        a1 = Article.insert_if_new(db_session, url="https://a.com/1", author_id=author.id, source_id=source.id, title="P1")
        a2 = Article.insert_if_new(db_session, url="https://a.com/2", author_id=author.id, source_id=source.id, title="P2")
        a1.mark_ai_result(db_session, summary="Summary", ai_relevant=True)
        unprocessed = Article.unprocessed(db_session)
        assert len(unprocessed) == 1
        assert unprocessed[0].url == "https://a.com/2"

    def test_unprocessed_excludes_not_relevant_articles(self, db_session):
        """Regression: articles the AI marked as not-relevant have summary=NULL
        but ai_relevant=False. These must NOT appear as unprocessed, otherwise
        the pipeline re-sends them to the AI on every hourly run."""
        author, source = self._make_author_source(db_session)
        a1 = Article.insert_if_new(db_session, url="https://a.com/1", author_id=author.id, source_id=source.id, title="P1")
        a2 = Article.insert_if_new(db_session, url="https://a.com/2", author_id=author.id, source_id=source.id, title="P2")
        a1.mark_ai_result(db_session, summary=None, ai_relevant=False)  # not relevant, no summary
        unprocessed = Article.unprocessed(db_session)
        assert len(unprocessed) == 1
        assert unprocessed[0].url == "https://a.com/2"

    def test_unfed(self, db_session):
        author, source = self._make_author_source(db_session)
        a1 = Article.insert_if_new(db_session, url="https://a.com/1", author_id=author.id, source_id=source.id, title="P1")
        a2 = Article.insert_if_new(db_session, url="https://a.com/2", author_id=author.id, source_id=source.id, title="P2")
        a1.mark_ai_result(db_session, summary="S1", ai_relevant=True)
        a2.mark_ai_result(db_session, summary="S2", ai_relevant=True)
        a1.mark_fed(db_session)
        unfed = Article.unfed(db_session)
        assert len(unfed) == 1
        assert unfed[0].url == "https://a.com/2"

    def test_for_digest(self, db_session):
        author, source = self._make_author_source(db_session)
        a1 = Article.insert_if_new(
            db_session, url="https://a.com/1", author_id=author.id, source_id=source.id,
            title="P1", fetched_at="2026-04-01T10:00:00Z",
        )
        a2 = Article.insert_if_new(
            db_session, url="https://a.com/2", author_id=author.id, source_id=source.id,
            title="P2", fetched_at="2026-04-08T10:00:00Z",
        )
        a1.mark_ai_result(db_session, summary="S1", ai_relevant=True)
        a2.mark_ai_result(db_session, summary="S2", ai_relevant=True)
        articles = Article.for_digest(db_session, "2026-03-30T00:00:00Z", "2026-04-05T00:00:00Z")
        assert len(articles) == 1
        assert articles[0].url == "https://a.com/1"

    def test_for_digest_excludes_already_digested(self, db_session):
        author, source = self._make_author_source(db_session)
        a1 = Article.insert_if_new(
            db_session, url="https://a.com/1", author_id=author.id, source_id=source.id,
            title="P1", fetched_at="2026-04-01T10:00:00Z",
        )
        a1.mark_ai_result(db_session, summary="S1", ai_relevant=True)
        Digest.create(db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
                       period_end="2026-04-05T00:00:00Z", global_summary="Week summary", articles=[a1])
        articles = Article.for_digest(db_session, "2026-03-30T00:00:00Z", "2026-04-05T00:00:00Z")
        assert len(articles) == 0

    def test_mark_ai_result(self, db_session):
        author, source = self._make_author_source(db_session)
        a = Article.insert_if_new(db_session, url="https://a.com/1", author_id=author.id, source_id=source.id, title="P1")
        a.mark_ai_result(db_session, summary="Great post about AI", ai_relevant=True)
        assert a.summary == "Great post about AI"
        assert a.ai_relevant is True

    def test_to_dict(self, db_session):
        author, source = self._make_author_source(db_session)
        a = Article.insert_if_new(
            db_session, url="https://a.com/1", author_id=author.id, source_id=source.id,
            title="P1", published_at="2026-04-01T10:00:00Z",
        )
        a.mark_ai_result(db_session, summary="Summary", ai_relevant=True)
        d = a.to_dict()
        assert d["author"] == "Alice"
        assert d["bio"] == "A dev"
        assert d["title"] == "P1"
        assert d["url"] == "https://a.com/1"
        assert d["summary"] == "Summary"


    def test_to_dict_uses_fetched_at_when_published_at_is_null(self, db_session):
        """Regression test: scraped articles (e.g. Paul Graham) have no publication
        date. Before the fix, to_dict() returned published_at=None, which caused
        the feed publisher to stamp them with datetime.now() — making old essays
        appear as if published today every time the feed was rebuilt.

        The fix: to_dict() falls back to fetched_at when published_at is NULL,
        so the article keeps a stable date (when it was first scraped).
        """
        author, source = self._make_author_source(db_session)
        a = Article.insert_if_new(
            db_session, url="https://paulgraham.com/writes.html",
            author_id=author.id, source_id=source.id,
            title="Writes and Write-Nots",
            published_at=None,  # scraped articles have no date
            fetched_at="2026-04-01T12:00:00Z",
        )
        a.mark_ai_result(db_session, summary="Summary", ai_relevant=True)
        d = a.to_dict()
        assert d["published_at"] == "2026-04-01T12:00:00Z", (
            "When published_at is NULL, to_dict() must fall back to fetched_at — "
            "not None (which would cause the feed to use today's date)"
        )

    def test_to_dict_prefers_published_at_over_fetched_at(self, db_session):
        """Ensure to_dict() uses the real published_at when it exists."""
        author, source = self._make_author_source(db_session)
        a = Article.insert_if_new(
            db_session, url="https://a.com/dated-post",
            author_id=author.id, source_id=source.id,
            title="Dated Post",
            published_at="2026-03-15T08:00:00Z",
            fetched_at="2026-04-01T12:00:00Z",
        )
        d = a.to_dict()
        assert d["published_at"] == "2026-03-15T08:00:00Z"


class TestDigest:
    def _make_articles(self, db_session, count=2):
        author = Author.upsert(db_session, slug="alice", name="Alice", bio="A dev", priority=1)
        source = Source.upsert(db_session, url="https://a.com/feed", author_id=author.id, type="rss", label="blog")
        articles = []
        for i in range(count):
            a = Article.insert_if_new(
                db_session, url=f"https://a.com/{i}", author_id=author.id, source_id=source.id,
                title=f"Post {i}", fetched_at=f"2026-04-0{i+1}T10:00:00Z",
            )
            a.mark_ai_result(db_session, summary=f"Summary {i}", ai_relevant=True)
            articles.append(a)
        return articles

    def test_create(self, db_session):
        articles = self._make_articles(db_session)
        digest = Digest.create(
            db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
            period_end="2026-04-06T00:00:00Z", global_summary="A great week", articles=articles,
        )
        assert digest.id is not None
        assert len(digest.articles) == 2

    def test_for_period_finds_sent(self, db_session):
        articles = self._make_articles(db_session)
        digest = Digest.create(
            db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
            period_end="2026-04-06T00:00:00Z", global_summary="Summary", articles=articles,
        )
        digest.emailed_at = "2026-04-07T08:00:00Z"
        db_session.flush()
        found = Digest.for_period(db_session, "2026-03-30T00:00:00Z", "2026-04-06T00:00:00Z")
        assert found is not None
        assert found.id == digest.id

    def test_for_period_ignores_unsent(self, db_session):
        articles = self._make_articles(db_session)
        Digest.create(
            db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
            period_end="2026-04-06T00:00:00Z", global_summary="Summary", articles=articles,
        )
        found = Digest.for_period(db_session, "2026-03-30T00:00:00Z", "2026-04-06T00:00:00Z")
        assert found is None

    def test_to_dict(self, db_session):
        articles = self._make_articles(db_session, count=1)
        digest = Digest.create(
            db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
            period_end="2026-04-06T00:00:00Z", global_summary="Great week", articles=articles,
        )
        d = digest.to_dict()
        assert d["label"] == "2026-CW14"
        assert d["global_summary"] == "Great week"
        assert len(d["articles"]) == 1
        assert d["articles"][0]["author"] == "Alice"

    def test_label_unique(self, db_session):
        articles = self._make_articles(db_session)
        Digest.create(db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
                       period_end="2026-04-06T00:00:00Z", global_summary="S", articles=articles)
        with pytest.raises(IntegrityError):
            Digest.create(db_session, label="2026-CW14", period_start="2026-03-30T00:00:00Z",
                           period_end="2026-04-06T00:00:00Z", global_summary="S2", articles=[])


class TestSyncSourcesFromYaml:
    def test_sync_creates_authors_and_sources(self, db_session, sources_config):
        sync_sources_from_yaml(db_session, sources_config)
        authors = db_session.query(Author).all()
        assert len(authors) == 2
        assert {a.slug for a in authors} == {"alice-engineer", "bob-coder"}
        sources = db_session.query(Source).all()
        assert len(sources) == 3

    def test_sync_updates_on_rerun(self, db_session, sources_config):
        sync_sources_from_yaml(db_session, sources_config)
        sources_config["engineers"][0]["bio"] = "Updated bio"
        sync_sources_from_yaml(db_session, sources_config)
        alice = db_session.query(Author).filter_by(slug="alice-engineer").one()
        assert alice.bio == "Updated bio"
        assert db_session.query(Author).count() == 2

    def test_sync_skip_attribute_disables(self, db_session):
        """skip: true in YAML sets enabled=False while preserving the source type."""
        config = {
            "engineers": [
                {
                    "slug": "test",
                    "name": "Test",
                    "priority": 1,
                    "sources": [
                        {"url": "https://skip.com/feed", "type": "rss", "label": "skipped", "skip": True},
                    ],
                }
            ]
        }
        sync_sources_from_yaml(db_session, config)
        source = db_session.query(Source).one()
        assert source.enabled is False
        assert source.type == "rss"

    def test_sync_skip_defaults_to_false(self, db_session):
        """Sources without skip attribute default to enabled=True."""
        config = {
            "engineers": [
                {
                    "slug": "test",
                    "name": "Test",
                    "priority": 1,
                    "sources": [
                        {"url": "https://active.com/feed", "type": "rss", "label": "blog"},
                    ],
                }
            ]
        }
        sync_sources_from_yaml(db_session, config)
        source = db_session.query(Source).one()
        assert source.enabled is True

    def test_sync_idempotent(self, db_session, sources_config):
        sync_sources_from_yaml(db_session, sources_config)
        sync_sources_from_yaml(db_session, sources_config)
        assert db_session.query(Author).count() == 2
        assert db_session.query(Source).count() == 3
