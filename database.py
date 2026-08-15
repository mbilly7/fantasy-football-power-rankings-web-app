import logging
import json
from datetime import datetime, timezone
import os
from sqlalchemy import create_engine, Column, Integer, DateTime, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)
Base = declarative_base()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///rankings.db")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

class WeeklyRankings(Base):
    __tablename__ = 'weekly_rankings'
    id = Column(Integer, primary_key=True)
    week = Column(Integer)
    season = Column(Integer)
    rankings = Column(Text)  # JSON string
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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