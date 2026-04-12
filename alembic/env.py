from logging.config import fileConfig
from pathlib import Path

import yaml
from sqlalchemy import engine_from_config, pool

from alembic import context

# Load our models so autogenerate can diff against them
from db.models import Base  # noqa: F401 — registers all models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    """Resolve DB URL from config.yaml, falling back to alembic.ini."""
    config_yaml = Path(__file__).parent.parent / "config.yaml"
    if config_yaml.exists():
        with open(config_yaml) as f:
            cfg = yaml.safe_load(f)
        db_path = cfg.get("paths", {}).get("db_path", "data/swe_ai_digest.db")
        return f"sqlite:///{db_path}"
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    url = _db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE support
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
