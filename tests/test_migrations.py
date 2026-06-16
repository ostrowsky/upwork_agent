"""Guards the migration path tests previously missed: ALTERing an EXISTING DB.

Unit tests build the schema from scratch (create_all), so they never catch a
model column added WITHOUT a matching migration. Here we create an old-schema
SQLite file, run the migration, and assert the new columns appear — and that
re-running is idempotent.
"""

import os
import sys

from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


def _cols(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_migration_adds_new_columns_to_existing_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    # Minimal OLD-schema tables, missing the columns added in later days.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, description TEXT, status TEXT)"))
        conn.execute(text("CREATE TABLE proposals (id INTEGER PRIMARY KEY, job_id INTEGER, status TEXT, content TEXT)"))
        conn.execute(text("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE client_messages (id INTEGER PRIMARY KEY, client_id INTEGER, direction TEXT, content TEXT)"))
        conn.execute(text("CREATE TABLE case_studies (id INTEGER PRIMARY KEY, title TEXT, description TEXT)"))

    monkeypatch.setattr(database, "engine", engine)

    database._migrate_sqlite()

    # Day 3/9 job columns
    assert {"task_id", "upwork_job_id", "outcome", "loss_reason", "revenue"} <= _cols(engine, "jobs")
    # Day 7 proposal columns
    assert {"connects_spent", "submitted_at"} <= _cols(engine, "proposals")
    # Day 8 client/message columns
    assert "external_id" in _cols(engine, "clients")
    assert "external_id" in _cols(engine, "client_messages")
    # Day 5 case column
    assert "deleted" in _cols(engine, "case_studies")

    # Idempotent: a second run must not raise.
    database._migrate_sqlite()
