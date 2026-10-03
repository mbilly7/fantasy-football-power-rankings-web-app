import logging
import json
from datetime import datetime, timezone
import os
from sqlalchemy import create_engine, inspect, Column, Integer, DateTime, Text, text, UniqueConstraint, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)
Base = declarative_base()

# Bound to an engine by init_db().
Session = sessionmaker()

# Bump whenever power_rankings_generator changes its output, so cached weeks are recomputed.
RANKINGS_VERSION = 2

class WeeklyRankings(Base):
    __tablename__ = 'weekly_rankings'
    __table_args__ = (
        UniqueConstraint("week", "season", name="uq_weekly_rankings_week_season"),
    )
    id = Column(Integer, primary_key=True)
    week = Column(Integer)
    season = Column(Integer)
    rankings = Column(Text)  # JSON string
    version = Column(Integer, nullable=True)  # RANKINGS_VERSION used to calculate the row
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SeasonSyncStatus(Base):
    __tablename__ = "season_sync_status"
    season = Column(Integer, primary_key=True)
    discovered = Column(Boolean, default=False)
    backfilled = Column(Boolean, default=False)
    max_week_backfilled = Column(Integer, nullable=True)
    status = Column(Text, default="unknown")
    last_checked_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_backfilled_at = Column(DateTime(timezone=True), nullable=True)


class SeasonWeeks(Base):
    """The completed weeks of a season as shown in the week picker, saved from the last ESPN fetch."""
    __tablename__ = "season_weeks"
    season = Column(Integer, primary_key=True)
    weeks = Column(Text)  # JSON list of {"week": int, "label": str}
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def _add_missing_columns(engine):
    """create_all() only creates missing tables, so add columns introduced after a table was created."""
    existing = {column["name"] for column in inspect(engine).get_columns("weekly_rankings")}
    if "version" not in existing:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE weekly_rankings ADD COLUMN version INTEGER"))
        logger.info("Added weekly_rankings.version column")


def init_db(database_url=None):
    """
    Connect to the database, create any missing tables, and bind Session to it.

    Args:
        database_url (str, optional): SQLAlchemy URL. Defaults to DATABASE_URL, then a local SQLite file.
            "sqlite://" gives an in-memory database shared by all sessions (useful for tests).

    Returns:
        Engine: The engine Session is now bound to.
    """
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///rankings.db")
    engine_options = {}
    if url in ("sqlite://", "sqlite:///:memory:"):
        # Each new connection to an in-memory SQLite DB would otherwise get its own empty database.
        engine_options = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    engine = create_engine(url, **engine_options)
    safe_url = engine.url.render_as_string(hide_password=True)

    try:
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Failed to connect to rankings database at %s", safe_url)
        raise

    Session.configure(bind=engine)
    logger.info("Connected to rankings database at %s", safe_url)
    return engine


def save_rankings(week, season, rankings):
    with Session() as session:
        deleted = session.query(WeeklyRankings).filter_by(week=week, season=season).delete()
        session.add(
            WeeklyRankings(week=week, season=season, rankings=json.dumps(rankings), version=RANKINGS_VERSION)
        )
        session.commit()
    logger.info(
        "Saved %s ranking row(s) for season=%s week=%s (replaced %s existing row(s))",
        len(rankings),
        season,
        week,
        deleted,
    )


def get_rankings(season, week):
    """
    Rankings for one week of a season, as (rankings, updated_at), or (None, None) if not cached.
    Rows calculated by an older RANKINGS_VERSION count as not cached.
    """
    with Session() as session:
        entry = (
            session.query(WeeklyRankings)
            .filter_by(season=season, week=week, version=RANKINGS_VERSION)
            .first()
        )

    if not entry:
        logger.info("No cached rankings found for season=%s week=%s", season, week)
        return None, None

    logger.info("Loaded cached rankings for season=%s week=%s updated_at=%s", season, week, entry.updated_at)
    return json.loads(entry.rankings), entry.updated_at


def get_available_seasons():
    with Session() as session:
        rows = session.query(WeeklyRankings.season).distinct().order_by(WeeklyRankings.season.desc()).all()
    seasons = [int(season) for (season,) in rows]
    logger.info("Loaded %s available season(s)", len(seasons))
    return seasons


def get_available_weeks(season):
    with Session() as session:
        rows = (
            session.query(WeeklyRankings.week)
            .filter_by(season=season)
            .distinct()
            .order_by(WeeklyRankings.week.asc())
            .all()
        )
    weeks = [int(week) for (week,) in rows]
    logger.info("Loaded %s available week(s) for season=%s", len(weeks), season)
    return weeks


def get_season_sync_status_map():
    with Session() as session:
        rows = session.query(SeasonSyncStatus).all()
        return {
            int(row.season): {
                "discovered": bool(row.discovered),
                "backfilled": bool(row.backfilled),
                "max_week_backfilled": row.max_week_backfilled,
                "status": row.status,
                "last_checked_at": row.last_checked_at,
            }
            for row in rows
        }


def upsert_season_sync_status(
    season,
    discovered=None,
    backfilled=None,
    max_week_backfilled=None,
    status=None,
    checked=True,
):
    """Create or update a season's sync row. Fields left as None are not changed."""
    now = datetime.now(timezone.utc)
    with Session() as session:
        row = session.query(SeasonSyncStatus).filter_by(season=season).first()
        if row is None:
            row = SeasonSyncStatus(season=season)
            session.add(row)

        if discovered is not None:
            row.discovered = bool(discovered)
        if backfilled is not None:
            row.backfilled = bool(backfilled)
        if max_week_backfilled is not None:
            row.max_week_backfilled = int(max_week_backfilled)
        if status is not None:
            row.status = status
        if checked:
            row.last_checked_at = now
        if backfilled:
            row.last_backfilled_at = now

        session.commit()


def get_discovered_seasons():
    with Session() as session:
        rows = (
            session.query(SeasonSyncStatus.season)
            .filter_by(discovered=True)
            .order_by(SeasonSyncStatus.season.desc())
            .all()
        )
    return [int(season) for (season,) in rows]


def save_season_weeks(season, weeks):
    with Session() as session:
        row = session.get(SeasonWeeks, season) or SeasonWeeks(season=season)
        row.weeks = json.dumps(weeks)
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()


def get_season_weeks(season):
    """The saved week picker options for a season, or None if the season hasn't been fetched yet."""
    with Session() as session:
        row = session.get(SeasonWeeks, season)
        return json.loads(row.weeks) if row else None
