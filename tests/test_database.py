import pytest
import json
from datetime import datetime, timezone
import database
from database import (
    Base,
    WeeklyRankings,
    save_rankings,
    get_rankings,
    get_available_seasons,
    get_available_weeks,
    get_season_sync_status_map,
    upsert_season_sync_status,
    get_discovered_seasons,
)


@pytest.fixture(autouse=True)
def test_db():
    """Point the database module at a fresh in-memory SQLite DB, then restore the previous binding."""
    previous_bind = database.Session.kw.get("bind")
    engine = database.init_db("sqlite://")
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()
    database.Session.configure(bind=previous_bind)


def test_save_rankings():
    """Test saving rankings to the database."""
    rankings = [
        {"team_id": 1, "team_name": "Team A", "pr_score": 10.5},
        {"team_id": 2, "team_name": "Team B", "pr_score": 20.3},
    ]

    save_rankings(week=1, season=2024, rankings=rankings)

    with database.Session() as session:
        entry = session.query(WeeklyRankings).filter_by(week=1, season=2024).first()

    assert entry is not None
    assert entry.week == 1
    assert entry.season == 2024
    assert json.loads(entry.rankings) == rankings


def test_save_rankings_overwrites_existing():
    """Test that saving rankings for the same week overwrites the previous entry."""
    rankings_v1 = [{"team_id": 1, "team_name": "Team A", "pr_score": 10}]
    rankings_v2 = [{"team_id": 1, "team_name": "Team A", "pr_score": 20}]

    save_rankings(week=1, season=2024, rankings=rankings_v1)
    save_rankings(week=1, season=2024, rankings=rankings_v2)

    with database.Session() as session:
        entries = session.query(WeeklyRankings).filter_by(week=1, season=2024).all()

    # Should only have one entry (the newer one)
    assert len(entries) == 1
    assert json.loads(entries[0].rankings) == rankings_v2


def test_rankings_json_serialization():
    """Test that complex ranking objects serialize/deserialize correctly."""
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
    result_rankings, _ = get_rankings(season=2024, week=5)

    assert result_rankings == rankings
    assert result_rankings[0]["ppg"] == 125.45
    assert result_rankings[0]["record"] == "10-2-1"


def test_multiple_seasons():
    """Test that rankings are correctly isolated by season."""
    rankings_2024 = [{"team_id": 1, "team_name": "Team A", "pr_score": 10}]
    rankings_2025 = [{"team_id": 1, "team_name": "Team A", "pr_score": 20}]

    save_rankings(week=1, season=2024, rankings=rankings_2024)
    save_rankings(week=1, season=2025, rankings=rankings_2025)

    result_2024, _ = get_rankings(season=2024, week=1)
    result_2025, _ = get_rankings(season=2025, week=1)

    assert result_2024 == rankings_2024
    assert result_2025 == rankings_2025


def test_updated_at_timestamp():
    """Test that updated_at timestamp is set on insertion."""
    rankings = [{"team_id": 1, "team_name": "Team A"}]
    before = datetime.now(timezone.utc)

    save_rankings(week=1, season=2024, rankings=rankings)

    after = datetime.now(timezone.utc)
    _, updated_at = get_rankings(season=2024, week=1)

    assert updated_at is not None
    # SQLite stores naive datetimes; compare without timezone info
    entry_time = updated_at.replace(tzinfo=None)
    assert before.replace(tzinfo=None) <= entry_time <= after.replace(tzinfo=None)


def test_get_rankings_by_week():
    week1 = [{"team_id": 1, "team_name": "Team A", "pr_score": 10}]
    week2 = [{"team_id": 1, "team_name": "Team A", "pr_score": 20}]
    save_rankings(week=1, season=2024, rankings=week1)
    save_rankings(week=2, season=2024, rankings=week2)

    result_week1, _ = get_rankings(season=2024, week=1)
    result_week2, _ = get_rankings(season=2024, week=2)

    assert result_week1 == week1
    assert result_week2 == week2


def test_get_rankings_missing():
    assert get_rankings(season=9999, week=1) == (None, None)


def test_get_available_seasons_and_weeks():
    save_rankings(week=1, season=2024, rankings=[{"team_id": 1}])
    save_rankings(week=3, season=2024, rankings=[{"team_id": 1}])
    save_rankings(week=2, season=2025, rankings=[{"team_id": 1}])

    assert get_available_seasons() == [2025, 2024]
    assert get_available_weeks(2024) == [1, 3]
    assert get_available_weeks(2025) == [2]


def test_season_sync_status_tracking():
    upsert_season_sync_status(2024, discovered=True, backfilled=False, status="available")
    upsert_season_sync_status(2023, discovered=False, status="http_404")
    upsert_season_sync_status(2024, backfilled=True, max_week_backfilled=7, status="backfilled")

    status_map = get_season_sync_status_map()

    assert 2024 in status_map
    assert status_map[2024]["discovered"] is True
    assert status_map[2024]["backfilled"] is True
    assert status_map[2024]["max_week_backfilled"] == 7
    assert get_discovered_seasons() == [2024]


def test_rankings_from_older_version_count_as_not_cached():
    save_rankings(week=1, season=2024, rankings=[{"team_id": 1}])
    with database.Session() as session:
        session.query(WeeklyRankings).update({"version": database.RANKINGS_VERSION - 1})
        session.commit()

    assert get_rankings(season=2024, week=1) == (None, None)


def test_season_weeks_round_trip():
    weeks = [{"week": 1, "label": "Week 1"}, {"week": 15, "label": "Playoffs Round 1 (Week 15)"}]

    assert database.get_season_weeks(2025) is None
    database.save_season_weeks(2025, weeks)
    database.save_season_weeks(2025, weeks[:1])
    assert database.get_season_weeks(2025) == weeks[:1]


def test_init_db_adds_version_column_to_existing_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path / 'old.db'}"
    with create_engine(url).begin() as connection:
        connection.execute(text(
            "CREATE TABLE weekly_rankings (id INTEGER PRIMARY KEY, week INTEGER, season INTEGER, "
            "rankings TEXT, updated_at DATETIME)"
        ))
        connection.execute(text("INSERT INTO weekly_rankings (week, season, rankings) VALUES (1, 2019, '[]')"))

    engine = database.init_db(url)

    assert "version" in {c["name"] for c in inspect(engine).get_columns("weekly_rankings")}
    assert get_rankings(season=2019, week=1) == (None, None)  # pre-version rows are recomputed
    engine.dispose()
