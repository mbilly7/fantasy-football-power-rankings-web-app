from sqlalchemy import create_engine, Column, Integer, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
import json
from datetime import datetime, timezone
import os

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

def save_rankings(week, season, rankings):
    session = Session()
    # Delete old entry for the week
    session.query(WeeklyRankings).filter_by(week=week, season=season).delete()
    # Insert new
    entry = WeeklyRankings(week=week, season=season, rankings=json.dumps(rankings))
    session.add(entry)
    session.commit()
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
        session.close()
        return None, None

    rankings = json.loads(entry.rankings)
    updated_at = entry.updated_at
    session.close()
    return rankings, updated_at