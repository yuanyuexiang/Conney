from alembic import context

from relay.config import get_settings
from relay.database.session import get_engine

if context.is_offline_mode():
    context.configure(url=get_settings().database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with get_engine().connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
