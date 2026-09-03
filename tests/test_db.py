"""Connection-level SQLite setup (PRAGMAs)."""

from sqlalchemy import text

from src.db import make_engine


def test_file_backed_db_uses_wal_and_fk_enforcement(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'yard.db'}")
    try:
        with engine.connect() as con:
            assert con.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
            assert con.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert con.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    finally:
        engine.dispose()


def test_in_memory_db_still_connects(tmp_path):
    # WAL is a no-op for :memory:; the pragma hook must not choke on it.
    engine = make_engine("sqlite://")
    try:
        with engine.connect() as con:
            assert con.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()
