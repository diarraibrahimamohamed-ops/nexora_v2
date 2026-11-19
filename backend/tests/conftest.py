"""Tests Nexora v2 — SQLite by default, PostgreSQL when configured."""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app

TEST_DB = os.getenv("NEXORA_TEST_DATABASE_URL", "sqlite:///./test_nexora.db")
if TEST_DB.startswith("sqlite"):
    engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
else:
    # Dedicated PostgreSQL CI gates exercise real FOR UPDATE / SKIP LOCKED semantics.
    engine = create_engine(TEST_DB, pool_pre_ping=True, pool_size=10, max_overflow=20)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
