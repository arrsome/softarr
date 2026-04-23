import uuid

from sqlalchemy import CHAR
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

from softarr.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class GUID(TypeDecorator):
    """Backend-agnostic UUID column type.

    Stores values as ``CHAR(36)`` strings on disk (SQLite-friendly) but
    exchanges ``uuid.UUID`` objects with Python code. Mirrors the "GUID"
    recipe from the SQLAlchemy documentation. When PostgreSQL support is
    re-added, this decorator can be extended to use the native ``UUID``
    type via ``load_dialect_impl``.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value)
        # Accept raw strings too -- validate by round-tripping through UUID.
        return str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
