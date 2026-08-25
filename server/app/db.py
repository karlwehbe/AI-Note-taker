from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # No migration tool for this small portfolio build — tables are created
    # directly from the models at startup if they don't already exist.
    # create_all does NOT add new columns to existing tables, so we also
    # apply a tiny set of additive ALTERs for columns that landed after the
    # first create (otherwise draft saves fail silently against old DBs).
    import app.models  # noqa: F401  (registers models on Base.metadata)

    # Runs before create_all: user_profiles was restructured for the personal
    # context layer (flat columns -> a jsonb `fields` blob, since two answers
    # are multi-select; profile_guidance -> nullable compiled_prompt; plus
    # is_edited). The old shape only ever held throwaway data, so it's dropped
    # and recreated rather than migrated column by column.
    with engine.begin() as conn:
        if conn.execute(text("SELECT to_regclass('public.user_profiles')")).scalar():
            legacy = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'user_profiles' AND column_name = 'profile_guidance'"
                )
            ).scalar()
            if legacy:
                conn.execute(text("DROP TABLE user_profiles"))

        # "Upload my notes" / notes_style_samples was removed end-to-end.
        # Drop leftover table + the old prompt_sections mirror if present.
        conn.execute(text("DROP TABLE IF EXISTS notes_style_samples"))
        conn.execute(text("DROP TABLE IF EXISTS prompt_sections"))

    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE user_profiles "
                "ADD COLUMN IF NOT EXISTS compile_failed_at TIMESTAMPTZ"
            )
        )

        conn.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN IF NOT EXISTS draft_transcript TEXT"
            )
        )

        # is_edited existed only to guard a hand-edited compiled prompt. The
        # compiled description is private now — there is nothing for the user
        # to edit, so nothing to protect.
        conn.execute(
            text("ALTER TABLE user_profiles DROP COLUMN IF EXISTS is_edited")
        )


def check_db_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
