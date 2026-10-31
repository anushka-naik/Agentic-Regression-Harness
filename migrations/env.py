"""Alembic environment. Reads DATABASE_URL from the environment or .env."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from agentcheck.settings import Settings
from agentcheck.storage.models import Base

config = context.config
url = Settings().database_url
if not url:
    raise SystemExit("DATABASE_URL is not set. Copy .env.example to .env first.")
config.set_main_option("sqlalchemy.url", url)
target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section, {}),
                                prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
