import os
from collections.abc import Iterator

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def build_database_url() -> str | URL:
    if database_url := os.getenv("DATABASE_URL"):
        return database_url
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "tracker"),
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "db"),
        database=os.getenv("POSTGRES_DB", "tracker"),
    )


engine = create_engine(build_database_url(), pool_pre_ping=True)
SessionFactory = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
