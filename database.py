import logging
import json
from datetime import datetime, timezone
import os
from sqlalchemy import create_engine, Column, Integer, DateTime, Text, text, UniqueConstraint, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)
Base = declarative_base()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///rankings.db")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

class WeeklyRankings(Base):
    __tablename__ = 'weekly_rankings'
    __table_args__ = (
        UniqueConstraint("week", "season", name="uq_weekly_rankings_week_season"),
    )
    id = Column(Integer, primary_key=True)
    week = Column(Integer)
    season = Column(Integer)
    rankings = Column(Text)  # JSON string
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

Base.metadata.create_all(engine)

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    logger.info("Connected to rankings database at %s", DATABASE_URL)
except Exception:
    logger.exception("Failed to connect to rankings database at %s", DATABASE_URL)
    raise

def save_rankings(week, season, rankings):
    session = Session()
    deleted = session.query(WeeklyRankings).filter_by(week=week, season=season).delete()
    entry = WeeklyRankings(week=week, season=season, rankings=json.dumps(rankings))
    session.add(entry)
    session.commit()
    logger.info(
        "Saved %s ranking row(s) for season=%s week=%s (replaced %s existing row(s))",
        len(rankings),
        season,
        week,
        deleted,
    )
    session.close()

def get_latest_rankings(season):
    session = Session()
    entry = (
        session.query(WeeklyRankings)
        .filter_by(season=season)
        .order_by(WeeklyRankings.updated_at.desc())
        .first()
    )

    if not entry:
        logger.info("No cached rankings found for season=%s", season)
        session.close()
        return None, None

    rankings = json.loads(entry.rankings)
    updated_at = entry.updated_at
    logger.info(
        "Loaded cached rankings for season=%s week=%s updated_at=%s",
        season,
        entry.week,
        updated_at,
    )
    session.close()
    return rankings, updated_at


def get_latest_rankings_entry(season):
    session = Session()
    entry = (
        session.query(WeeklyRankings)
        .filter_by(season=season)
        .order_by(WeeklyRankings.week.desc(), WeeklyRankings.updated_at.desc())
        .first()
    )

    if not entry:
        logger.info("No latest rankings entry found for season=%s", season)
        session.close()
        return None, None, None

    rankings = json.loads(entry.rankings)
    updated_at = entry.updated_at
    week = entry.week
    logger.info(
        "Loaded latest rankings entry for season=%s week=%s updated_at=%s",
        season,
        week,
        updated_at,
    )
    session.close()
    return rankings, updated_at, week


def get_rankings(season, week):
    session = Session()
    entry = (
        session.query(WeeklyRankings)
        .filter_by(season=season, week=week)
        .order_by(WeeklyRankings.updated_at.desc())
        .first()
    )

    if not entry:
        logger.info("No cached rankings found for season=%s week=%s", season, week)
        session.close()
        return None, None

    rankings = json.loads(entry.rankings)
    updated_at = entry.updated_at
    logger.info(
        "Loaded cached rankings for season=%s week=%s updated_at=%s",
        season,
        week,
        updated_at,
    )
    session.close()
    return rankings, updated_at


def get_available_seasons():
    session = Session()
    seasons = [
        int(season)
        for (season,) in session.query(WeeklyRankings.season)
        .distinct()
        .order_by(WeeklyRankings.season.desc())
        .all()
    ]
    session.close()
    logger.info("Loaded %s available season(s)", len(seasons))
    return seasons


def get_available_weeks(season):
    session = Session()
    weeks = [
        int(week)
        for (week,) in session.query(WeeklyRankings.week)
        .filter_by(season=season)
        .distinct()
        .order_by(WeeklyRankings.week.asc())
        .all()
    ]
    session.close()
    logger.info("Loaded %s available week(s) for season=%s", len(weeks), season)
    return weeks


def get_season_sync_status_map():
    session = Session()
    rows = session.query(SeasonSyncStatus).all()
    status_map = {
        int(row.season): {
            "discovered": bool(row.discovered),
            "backfilled": bool(row.backfilled),
            "max_week_backfilled": row.max_week_backfilled,
            "status": row.status,
        }
        for row in rows
    }
    session.close()
    return status_map


def upsert_season_sync_status(
    season,
    discovered=None,
    backfilled=None,
    max_week_backfilled=None,
    status=None,
    checked=True,
):
    session = Session()
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
        row.last_checked_at = datetime.now(timezone.utc)
    if backfilled:
        row.last_backfilled_at = datetime.now(timezone.utc)

    session.commit()
    session.close()


def get_discovered_seasons():
    session = Session()
    seasons = [
        int(season)
        for (season,) in session.query(SeasonSyncStatus.season)
        .filter_by(discovered=True)
        .order_by(SeasonSyncStatus.season.desc())
        .all()
    ]
    session.close()
    return seasons