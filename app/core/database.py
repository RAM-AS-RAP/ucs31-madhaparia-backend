from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


class SchemaNotReadyError(RuntimeError):
    """Structural readiness failure, distinct from a programming error."""


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def check_schema():
    """Check that tables and columns required by the ORM exist.

    This is a read-only structural check, not a complete migration,
    column-type or constraint validation. Alembic manages schema changes.
    """
    from app.db import models  # noqa: F401

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    missing_tables = sorted(
        table.name
        for table in Base.metadata.tables.values()
        if table.name not in existing_tables
    )

    if missing_tables:
        raise SchemaNotReadyError(
            "The database schema is not up to date "
            f"(missing tables: {missing_tables}). "
            "Run: python -m alembic upgrade head"
        )

    missing_columns = []

    for table in Base.metadata.sorted_tables:
        existing_columns = {
            column["name"]
            for column in inspector.get_columns(table.name)
        }

        missing_columns.extend(
            f"{table.name}.{column.name}"
            for column in table.columns
            if column.name not in existing_columns
        )

    if missing_columns:
        raise SchemaNotReadyError(
            "The database schema is not up to date "
            f"(missing columns: {sorted(missing_columns)}). "
            "Run: python -m alembic upgrade head"
        )
