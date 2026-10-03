import os

# Keep app.py's import-time init_db() off the real rankings.db. Set before any test module imports app.
os.environ["DATABASE_URL"] = "sqlite://"

import pytest
import database


@pytest.fixture(autouse=True)
def test_db():
    """Point the database module at a fresh in-memory SQLite DB for every test, then restore the previous binding."""
    previous_bind = database.Session.kw.get("bind")
    engine = database.init_db("sqlite://")
    yield engine
    database.Base.metadata.drop_all(engine)
    engine.dispose()
    database.Session.configure(bind=previous_bind)
