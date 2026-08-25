"""Shared fixtures.

The integration and system layers need a real Postgres: the models use JSONB
and the postgresql UUID type, so SQLite-in-memory cannot stand in for them.

Tests connect to DATABASE_URL_TEST, defaulting to the local compose database
with a *separate* `ai_note_taker_test` database. Your dev data is never
touched — the fixtures only ever create, truncate, and drop tables inside
that test database. CI points the same variable at its own service container.
"""

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+psycopg://postgres:postgres@localhost:5432/ai_note_taker_test",
)


def _ensure_test_database_exists(url: str) -> None:
    """CREATE DATABASE if missing, connecting via the maintenance database.

    Guard rail: refuse to operate on anything not obviously a test database,
    so a mistyped DATABASE_URL_TEST can't drop real tables.
    """
    parsed = make_url(url)
    name = parsed.database or ""
    if "test" not in name:
        raise RuntimeError(
            f"Refusing to run tests against database {name!r} — the name must contain 'test'. "
            "Set DATABASE_URL_TEST to a dedicated test database."
        )

    admin_url = parsed.set(database="postgres")
    # AUTOCOMMIT: CREATE DATABASE cannot run inside a transaction.
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    try:
        _ensure_test_database_exists(TEST_DATABASE_URL)
    except Exception as exc:  # pragma: no cover - environment problem, not a test failure
        pytest.skip(f"Postgres unavailable for integration tests: {exc}")

    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    from app.db import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)

    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Generator[Session, None, None]:
    """A session whose writes are rolled back afterwards.

    Every test starts from an empty database. Truncating rather than relying
    on transaction nesting keeps this honest even for the endpoints that
    commit several times in one request — send_message commits three.
    """
    from app.db import Base

    with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        if tables:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """The app wired to the test database.

    get_db is overridden rather than reconfigured globally so the real engine
    is never pointed at the test database by accident.
    """
    from app.db import get_db
    from app.main import app

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    # Deliberately NOT used as a context manager. Entering one runs the
    # lifespan, and this app's lifespan calls init_db(), which creates tables
    # and runs ALTERs against the *real* engine from settings.database_url —
    # i.e. the dev database. The fixtures above own the schema instead.
    # raise_server_exceptions=False so a 500 is asserted as a response rather
    # than re-raised into the test, which is what a real client would see.
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
