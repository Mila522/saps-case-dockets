from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.base import Base
from app.db import models  # noqa: F401 -- register all model metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Bypass ConfigParser interpolation so percent-encoded passwords remain intact.
migration_url = make_url(settings.migration_database_url)


def include_name(name, type_, parent_names):
    if type_ == 'schema':
        return name == 'case_mgmt'
    return True


def configure_options() -> dict:
    return {
        'target_metadata': target_metadata,
        'include_schemas': True,
        'include_name': include_name,
        'version_table_schema': 'case_mgmt',
        'compare_type': True,
        'compare_server_default': True,
    }


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        **configure_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Keep case_mgmt explicit during reflection, even when the role defaults to it.
    connectable = create_engine(
        migration_url, poolclass=pool.NullPool,
        connect_args={'options': '-csearch_path=public'},
    )
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, **configure_options())
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
