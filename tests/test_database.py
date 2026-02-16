import pytest
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import database
from database import Base, WeeklyRankings, save_rankings, get_latest_rankings


@pytest.fixture
def test_db():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield engine, Session
    Base.metadata.drop_all(engine)


def test_save_rankings(test_db):
    """Test saving rankings to the database."""
    _, Session = test_db

    # Temporarily patch the module-level Session to use test session
    original_session = database.Session
    database.Session = Session

    try:
        rankings = [
            {"team_id": 1, "team_name": "Team A", "pr_score": 10.5},
            {"team_id": 2, "team_name": "Team B", "pr_score": 20.3},
        ]

        save_rankings(week=1, season=2024, rankings=rankings)

        # Verify the data was saved
        session = Session()
        entry = session.query(WeeklyRankings).filter_by(week=1, season=2024).first()
        session.close()

        assert entry is not None
        assert entry.week == 1
        assert entry.season == 2024
        assert json.loads(entry.rankings) == rankings
    finally:
        database.Session = original_session


def test_get_latest_rankings(test_db):
    """Test retrieving the latest rankings for a season."""
    _, Session = test_db

    original_session = database.Session
    database.Session = Session

    try:
        rankings_week1 = [
            {"team_id": 1, "team_name": "Team A", "pr_score": 10.5},
        ]
        rankings_week2 = [
            {"team_id": 1, "team_name": "Team A", "pr_score": 15.2},
        ]

        save_rankings(week=1, season=2024, rankings=rankings_week1)
        save_rankings(week=2, season=2024, rankings=rankings_week2)

        # Should retrieve week 2 (latest)
        result_rankings, result_updated_at = get_latest_rankings(season=2024)

        assert result_rankings == rankings_week2
        assert result_updated_at is not None
    finally:
        database.Session = original_session


def test_get_latest_rankings_empty(test_db):
    """Test retrieving rankings when none exist."""
    _, Session = test_db

    original_session = database.Session
    database.Session = Session

    try:
        result_rankings, result_updated_at = get_latest_rankings(season=9999)
        assert result_rankings is None
        assert result_updated_at is None
    finally:
        database.Session = original_session


def test_save_rankings_overwrites_existing(test_db):
    """Test that saving rankings for the same week overwrites the previous entry."""
    _, Session = test_db

    original_session = database.Session
    database.Session = Session

    try:
        rankings_v1 = [{"team_id": 1, "team_name": "Team A", "pr_score": 10}]
        rankings_v2 = [{"team_id": 1, "team_name": "Team A", "pr_score": 20}]

        save_rankings(week=1, season=2024, rankings=rankings_v1)
        save_rankings(week=1, season=2024, rankings=rankings_v2)

        session = Session()
        entries = session.query(WeeklyRankings).filter_by(week=1, season=2024).all()
        session.close()

        # Should only have one entry (the newer one)
        assert len(entries) == 1
        assert json.loads(entries[0].rankings) == rankings_v2
    finally:
        database.Session = original_session


def test_rankings_json_serialization(test_db):
    """Test that complex ranking objects serialize/deserialize correctly."""
    _, Session = test_db

    original_session = database.Session
    database.Session = Session

    try:
        rankings = [
            {
                "team_id": 1,
                "team_name": "Team A",
                "record": "10-2-1",
                "pr_score": 10.5,
                "ppg": 125.45,
                "consistency": 3,
                "overall_wins": "7-5",
                "ros_strength": 52,
            },
        ]

        save_rankings(week=5, season=2024, rankings=rankings)
        result_rankings, _ = get_latest_rankings(season=2024)

        assert result_rankings == rankings
        assert result_rankings[0]["ppg"] == 125.45
        assert result_rankings[0]["record"] == "10-2-1"
    finally:
        database.Session = original_session


def test_multiple_seasons(test_db):
    """Test that rankings are correctly isolated by season."""
    _, Session = test_db

    original_session = database.Session
    database.Session = Session

    try:
        rankings_2024 = [{"team_id": 1, "team_name": "Team A", "pr_score": 10}]
        rankings_2025 = [{"team_id": 1, "team_name": "Team A", "pr_score": 20}]

        save_rankings(week=1, season=2024, rankings=rankings_2024)
        save_rankings(week=1, season=2025, rankings=rankings_2025)

        result_2024, _ = get_latest_rankings(season=2024)
        result_2025, _ = get_latest_rankings(season=2025)

        assert result_2024 == rankings_2024
        assert result_2025 == rankings_2025
    finally:
        database.Session = original_session


def test_updated_at_timestamp(test_db):
    """Test that updated_at timestamp is set on insertion."""
    _, Session = test_db

    original_session = database.Session
    database.Session = Session

    try:
        rankings = [{"team_id": 1, "team_name": "Team A"}]
        before = datetime.now(timezone.utc)

        save_rankings(week=1, season=2024, rankings=rankings)

        after = datetime.now(timezone.utc)

        session = Session()
        entry = session.query(WeeklyRankings).filter_by(week=1, season=2024).first()
        session.close()

        assert entry.updated_at is not None
        # SQLite stores naive datetimes; compare without timezone info
        entry_time = entry.updated_at.replace(tzinfo=None) if entry.updated_at.tzinfo else entry.updated_at
        before_naive = before.replace(tzinfo=None)
        after_naive = after.replace(tzinfo=None)
        assert before_naive <= entry_time <= after_naive
    finally:
        database.Session = original_session
