import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, Text, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

logger = logging.getLogger(__name__)

Base = declarative_base()


class Deal(Base):
    __tablename__ = "deals"

    url = Column(String, primary_key=True)
    source = Column(String, nullable=False, default="ptt")
    title = Column(Text)
    body_content = Column(Text)
    parsed_json = Column(Text)
    status = Column(String, default="available")  # 'available' | 'sold'
    first_seen = Column(DateTime)   # set on INSERT, never overwritten
    updated_at = Column(DateTime)   # refreshed on every upsert


class DBManager:
    def __init__(self):
        db_url = os.getenv("DATABASE_URL", "sqlite:///./mac_deals.db")
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self._migrate_db()
        self.Session = sessionmaker(bind=self.engine)

    def _migrate_db(self):
        """Add columns that didn't exist in pre-P1 databases (SQLite ALTER TABLE)."""
        new_columns = {
            "source":     "ALTER TABLE deals ADD COLUMN source TEXT NOT NULL DEFAULT 'ptt'",
            "status":     "ALTER TABLE deals ADD COLUMN status TEXT DEFAULT 'available'",
            "first_seen": "ALTER TABLE deals ADD COLUMN first_seen TIMESTAMP",
            "updated_at": "ALTER TABLE deals ADD COLUMN updated_at TIMESTAMP",
        }
        with self.engine.connect() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(deals)"))}
            for col, stmt in new_columns.items():
                if col not in existing:
                    conn.execute(text(stmt))
                    logger.info("DB migration: added column '%s'", col)
            conn.commit()

    def get_cached_deal(self, url: str) -> Optional[Dict]:
        with self.Session() as session:
            deal = session.get(Deal, url)
            if deal:
                return {
                    "title": deal.title,
                    "body_content": deal.body_content,
                    "parsed_json": json.loads(deal.parsed_json) if deal.parsed_json else None,
                    "status": deal.status,
                }
        return None

    def save_deal(
        self,
        url: str,
        title: str,
        body_content: str,
        parsed_json: Optional[Dict] = None,
        status: str = "available",
        source: str = "ptt",
    ):
        now = datetime.now(timezone.utc)

        # Four-tuple dedup: skip write if (chip, ram_gb, ssd_gb, price) unchanged
        if parsed_json is not None:
            cached = self.get_cached_deal(url)
            if cached and cached.get("parsed_json"):
                old = cached["parsed_json"]
                old_tuple = (old.get("chip"), old.get("ram_gb"), old.get("ssd_gb"), old.get("price"))
                new_tuple = (parsed_json.get("chip"), parsed_json.get("ram_gb"),
                             parsed_json.get("ssd_gb"), parsed_json.get("price"))
                if old_tuple == new_tuple and None not in new_tuple:
                    return

        try:
            with self.Session() as session:
                existing = session.get(Deal, url)
                if existing:
                    existing.title = title
                    existing.body_content = body_content
                    if parsed_json is not None:
                        existing.parsed_json = json.dumps(parsed_json, ensure_ascii=False)
                    existing.status = status
                    existing.updated_at = now
                    # first_seen is intentionally NOT touched
                else:
                    session.add(Deal(
                        url=url,
                        source=source,
                        title=title,
                        body_content=body_content,
                        parsed_json=json.dumps(parsed_json, ensure_ascii=False) if parsed_json else None,
                        status=status,
                        first_seen=now,
                        updated_at=now,
                    ))
                session.commit()
        except Exception as e:
            logger.error("DB write error for %s: %s", url, e)

    def get_all_deals(self) -> List[Dict]:
        """Returns all deals as raw dicts (parsed_json kept as JSON string)."""
        try:
            with self.Session() as session:
                deals = session.query(Deal).all()
                return [
                    {
                        "url": d.url,
                        "title": d.title,
                        "body_content": d.body_content,
                        "parsed_json": d.parsed_json,
                        "status": d.status,
                    }
                    for d in deals
                ]
        except Exception as e:
            logger.error("DB bulk read error: %s", e)
            return []

    def get_filtered_deals(
        self,
        status: str = "available",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        ram_gb: Optional[int] = None,
        chip: Optional[str] = None,
        source: Optional[str] = None,
        screen_size: Optional[int] = None,
        model_type: Optional[str] = None,
    ) -> List[Dict]:
        """Query deals with optional filters. status/source filtered in SQL; others in Python."""
        try:
            with self.Session() as session:
                query = session.query(Deal).filter(Deal.parsed_json.isnot(None))
                if status:
                    query = query.filter(Deal.status == status)
                if source:
                    query = query.filter(Deal.source == source)
                deals = query.all()

            result = []
            for d in deals:
                item = json.loads(d.parsed_json)
                price = item.get("price")
                if min_price is not None and (not price or float(price) < min_price):
                    continue
                if max_price is not None and (not price or float(price) > max_price):
                    continue
                if ram_gb is not None and item.get("ram_gb") != ram_gb:
                    continue
                if chip is not None and chip.lower() not in str(item.get("chip", "")).lower():
                    continue
                if model_type == "Air" and item.get("series") != "Air":
                    continue
                if model_type == "Pro" and item.get("series") not in ["Pro 13", "Pro 14/16"]:
                    continue
                if screen_size is not None:
                    ss = item.get("screen_size")
                    if not ss or int(ss) != screen_size:
                        continue
                price = item.get("price")
                if not price or float(price) < 5000 or float(price) > 250000:
                    continue
                item["original_title"] = d.title
                item["url"] = d.url
                item["status"] = d.status
                result.append(item)
            return result
        except Exception as e:
            logger.error("DB filtered read error: %s", e)
            return []

    def get_all_parsed_deals(self) -> List[Dict]:
        """Returns deals that have been successfully parsed, with parsed_json expanded."""
        try:
            with self.Session() as session:
                deals = session.query(Deal).filter(Deal.parsed_json.isnot(None)).all()
                result = []
                for d in deals:
                    item = json.loads(d.parsed_json)
                    item["original_title"] = d.title
                    item["url"] = d.url
                    item["status"] = d.status
                    result.append(item)
                return result
        except Exception as e:
            logger.error("DB parsed read error: %s", e)
            return []


if __name__ == "__main__":
    db = DBManager()
    print("Database initialized.")
