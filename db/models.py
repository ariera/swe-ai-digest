"""SQLAlchemy ORM models for the SWE AI Digest pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.orm import DeclarativeBase, Session, relationship


class Base(DeclarativeBase):
    pass


digest_articles = Table(
    "digest_articles",
    Base.metadata,
    Column("digest_id", Integer, ForeignKey("digests.id"), primary_key=True),
    Column("article_id", Integer, ForeignKey("articles.id"), primary_key=True),
)


class Author(Base):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    bio = Column(Text)
    priority = Column(Integer)

    sources = relationship("Source", back_populates="author")
    articles = relationship("Article", back_populates="author")

    @classmethod
    def upsert(cls, session: Session, slug: str, name: str, bio: str | None, priority: int | None) -> Author:
        author = session.query(cls).filter_by(slug=slug).first()
        if author is None:
            author = cls(slug=slug, name=name, bio=bio, priority=priority)
            session.add(author)
        else:
            author.name = name
            author.bio = bio
            author.priority = priority
        session.flush()
        return author

    def to_dict(self) -> dict:
        return {"name": self.name, "bio": self.bio, "priority": self.priority}


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    url = Column(String, unique=True, nullable=False)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=True)
    type = Column(String, nullable=False)
    label = Column(String)
    enabled = Column(Boolean, nullable=False, default=True)
    last_fetched_at = Column(String)
    last_error = Column(Text)

    author = relationship("Author", back_populates="sources")

    @classmethod
    def upsert(cls, session: Session, url: str, author_id: int | None, type: str, label: str | None, enabled: bool = True) -> Source:
        source = session.query(cls).filter_by(url=url).first()
        if source is None:
            source = cls(url=url, author_id=author_id, type=type, label=label, enabled=enabled)
            session.add(source)
        else:
            source.author_id = author_id
            source.type = type
            source.label = label
            source.enabled = enabled
        session.flush()
        return source

    @classmethod
    def enabled_sources(cls, session: Session) -> list[Source]:
        return session.query(cls).filter_by(enabled=True).all()

    def record_fetch(self, session: Session, error: str | None = None) -> None:
        self.last_fetched_at = _now_iso()
        self.last_error = error
        session.flush()

    @classmethod
    def fetch_health(cls, session: Session) -> tuple[int, int]:
        total = session.query(cls).filter_by(enabled=True).count()
        errored = session.query(cls).filter(cls.enabled == True, cls.last_error.isnot(None)).count()  # noqa: E712
        return total, errored


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True)
    url = Column(String, unique=True, nullable=False)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"))
    title = Column(String, nullable=False)
    published_at = Column(String)
    fetched_at = Column(String, nullable=False)
    raw_content = Column(Text)
    summary = Column(Text)
    ai_relevant = Column(Boolean)
    feed_at = Column(String)
    source_type = Column(String)    # 'rss', 'scrape', 'podcast'
    attribution = Column(String)    # e.g. "Featured in Lex Fridman Podcast" — NULL for regular articles

    author = relationship("Author", back_populates="articles")
    source = relationship("Source")
    digests = relationship("Digest", secondary=digest_articles, back_populates="articles")

    @classmethod
    def insert_if_new(
        cls,
        session: Session,
        url: str,
        author_id: int,
        source_id: int | None,
        title: str,
        published_at: str | None = None,
        fetched_at: str | None = None,
        raw_content: str | None = None,
        source_type: str | None = None,
        attribution: str | None = None,
    ) -> Article | None:
        existing = session.query(cls).filter_by(url=url).first()
        if existing is not None:
            return None
        article = cls(
            url=url,
            author_id=author_id,
            source_id=source_id,
            title=title,
            published_at=published_at,
            fetched_at=fetched_at or _now_iso(),
            raw_content=raw_content,
            source_type=source_type,
            attribution=attribution,
        )
        session.add(article)
        session.flush()
        return article

    @classmethod
    def unprocessed(cls, session: Session) -> list[Article]:
        return session.query(cls).filter(cls.ai_relevant.is_(None)).all()

    @classmethod
    def unfed(cls, session: Session) -> list[Article]:
        return session.query(cls).filter(cls.ai_relevant == True, cls.feed_at.is_(None)).all()  # noqa: E712

    @classmethod
    def for_digest(cls, session: Session, period_start: str, period_end: str) -> list[Article]:
        # Use published_at when available; fall back to fetched_at for scraped
        # articles that have no publication date.
        effective_date = func.coalesce(cls.published_at, cls.fetched_at)
        return (
            session.query(cls)
            .filter(
                cls.ai_relevant == True,  # noqa: E712
                effective_date >= period_start,
                effective_date <= period_end,
                ~cls.digests.any(),
            )
            .all()
        )

    def mark_ai_result(self, session: Session, summary: str | None, ai_relevant: bool) -> None:
        self.summary = summary
        self.ai_relevant = ai_relevant
        session.flush()

    def mark_fed(self, session: Session, timestamp: str | None = None) -> None:
        self.feed_at = timestamp or _now_iso()
        session.flush()

    def to_dict(self) -> dict:
        return {
            "author": self.author.name,
            "bio": self.author.bio,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at or self.fetched_at,
            "summary": self.summary,
            "source_type": self.source_type,
            "attribution": self.attribution,
        }


class Digest(Base):
    __tablename__ = "digests"

    id = Column(Integer, primary_key=True)
    label = Column(String, unique=True, nullable=False)
    period_start = Column(String, nullable=False)
    period_end = Column(String, nullable=False)
    global_summary = Column(Text)
    created_at = Column(String, nullable=False)
    emailed_at = Column(String)
    page_at = Column(String)

    articles = relationship("Article", secondary=digest_articles, back_populates="digests")

    @classmethod
    def for_period(cls, session: Session, period_start: str, period_end: str) -> Digest | None:
        return (
            session.query(cls)
            .filter(
                cls.period_start <= period_end,
                cls.period_end >= period_start,
                cls.emailed_at.isnot(None),
            )
            .first()
        )

    @classmethod
    def create(
        cls,
        session: Session,
        label: str,
        period_start: str,
        period_end: str,
        global_summary: str,
        articles: list[Article],
    ) -> Digest:
        digest = cls(
            label=label,
            period_start=period_start,
            period_end=period_end,
            global_summary=global_summary,
            created_at=_now_iso(),
        )
        digest.articles = articles
        session.add(digest)
        session.flush()
        return digest

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "global_summary": self.global_summary,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "articles": [a.to_dict() for a in self.articles],
        }


def sync_sources_from_yaml(session: Session, sources_config: dict) -> None:
    for eng in sources_config.get("engineers", []):
        slug = eng["slug"]
        author = Author.upsert(
            session,
            slug=slug,
            name=eng["name"],
            bio=eng.get("bio"),
            priority=eng.get("priority"),
        )
        for src in eng.get("sources", []):
            Source.upsert(
                session,
                url=src["url"],
                author_id=author.id,
                type=src["type"],
                label=src.get("label"),
                enabled=not src.get("skip", False),
            )
    for src in sources_config.get("global_sources", []):
        Source.upsert(
            session,
            url=src["url"],
            author_id=None,
            type=src["type"],
            label=src.get("label"),
            enabled=not src.get("skip", False),
        )
    session.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
