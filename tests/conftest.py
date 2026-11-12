"""Tests use a disposable PostgreSQL schema, never the application's public schema."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import db, main, seed


@pytest.fixture
def client(monkeypatch):
    schema = "test_" + uuid4().hex
    admin = create_engine(db.DATABASE_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        db.DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_size=10,
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(main, "Session", factory)
    monkeypatch.setattr(seed, "Session", factory)
    # Use the real migration, including append-only database triggers.
    command.upgrade(Config("alembic.ini"), "head")
    seed.seed()
    with TestClient(main.app) as test_client:
        test_client.db_factory = factory
        yield test_client
    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()
