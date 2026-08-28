import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, text
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
    status = Column(String, default="available")  # 'available' | 'sold' | 'unavailable'
    first_seen = Column(DateTime)        # set on INSERT, never overwritten
    updated_at = Column(DateTime)        # refreshed when tracked fields actually change
    last_seen = Column(DateTime)         # refreshed on every scrape sighting (even no field change)
    last_alerted_price = Column(Integer) # last price for which a notifier alert was sent


# What each filter value in the sidebar accepts. A mapping rather than a chain
# of ifs, so adding a family means adding a line here — the Neo was missing
# from the chain and could not be filtered for at all.
_MODEL_TYPE_SERIES = {
    "Air": ("Air",),
    "Pro": ("Pro 13", "Pro 14/16"),
    "Neo": ("Neo",),
}


class DBManager:
    def __init__(self):
        db_url = os.getenv("DATABASE_URL", "sqlite:///./mac_deals.db")
        self.engine = create_engine(db_url, echo=False, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        self._migrate_db()
        self.Session = sessionmaker(bind=self.engine)

    def _migrate_db(self):
        """Add columns that didn't exist in pre-P1 databases."""
        new_columns = {
            "source":             "ALTER TABLE deals ADD COLUMN source TEXT NOT NULL DEFAULT 'ptt'",
            "status":             "ALTER TABLE deals ADD COLUMN status TEXT DEFAULT 'available'",
            "first_seen":         "ALTER TABLE deals ADD COLUMN first_seen TIMESTAMP",
            "updated_at":         "ALTER TABLE deals ADD COLUMN updated_at TIMESTAMP",
            "last_seen":          "ALTER TABLE deals ADD COLUMN last_seen TIMESTAMP",
            "last_alerted_price": "ALTER TABLE deals ADD COLUMN last_alerted_price INTEGER",
        }
        dialect = self.engine.dialect.name
        with self.engine.connect() as conn:
            if dialect == "sqlite":
                existing = {row[1] for row in conn.execute(text("PRAGMA table_info(deals)"))}
            else:
                rows = conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'deals'"
                ))
                existing = {row[0] for row in rows}
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

        # Four-tuple dedup: skip heavy field writes if (chip, ram_gb, ssd_gb, price) unchanged.
        # We still refresh last_seen below so sweep / freshness reporting stays accurate.
        skip_field_update = False
        if parsed_json is not None:
            cached = self.get_cached_deal(url)
            if cached and cached.get("parsed_json"):
                old = cached["parsed_json"]
                old_tuple = (old.get("chip"), old.get("ram_gb"), old.get("ssd_gb"), old.get("price"))
                new_tuple = (parsed_json.get("chip"), parsed_json.get("ram_gb"),
                             parsed_json.get("ssd_gb"), parsed_json.get("price"))
                if old_tuple == new_tuple and None not in new_tuple:
                    skip_field_update = True

        try:
            with self.Session() as session:
                existing = session.get(Deal, url)
                if existing:
                    if not skip_field_update:
                        existing.title = title
                        existing.body_content = body_content
                        if parsed_json is not None:
                            existing.parsed_json = json.dumps(parsed_json, ensure_ascii=False)
                        existing.status = status
                        existing.updated_at = now
                    existing.last_seen = now
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
                        last_seen=now,
                    ))
                session.commit()
        except Exception as e:
            logger.error("DB write error for %s: %s", url, e)

    def update_parsed(self, url: str, parsed_json: Dict) -> bool:
        """Replace a row's parsed spec without claiming the listing was seen.

        save_deal() records a sighting: it refreshes last_seen, which sweep_stale
        reads as "this listing was still on the platform at that moment". The
        parse step has no such evidence — it re-reads text already in the
        database and never visits the site.

        Using save_deal there made every row that failed to parse immortal: it
        was rewritten each night, last_seen moved forward, and the sweep could
        never age it out. 121 of 186 rows were in that state.
        """
        try:
            with self.Session() as session:
                deal = session.get(Deal, url)
                if not deal:
                    return False
                deal.parsed_json = json.dumps(parsed_json, ensure_ascii=False)
                deal.updated_at = datetime.now(timezone.utc)
                session.commit()
                return True
        except Exception as e:
            logger.error("DB update_parsed error for %s: %s", url, e)
            return False

    def update_last_alerted_price(self, url: str, price: int) -> bool:
        """Persist the price at which a notifier alert was just sent."""
        try:
            with self.Session() as session:
                deal = session.get(Deal, url)
                if not deal:
                    return False
                deal.last_alerted_price = int(price)
                session.commit()
                return True
        except Exception as e:
            logger.error("DB update_last_alerted_price error for %s: %s", url, e)
            return False

    def get_alert_state(self, url: str) -> Optional[Dict]:
        """Return {'price': int|None, 'last_alerted_price': int|None} for the given URL."""
        try:
            with self.Session() as session:
                deal = session.get(Deal, url)
                if not deal:
                    return None
                parsed = json.loads(deal.parsed_json) if deal.parsed_json else {}
                return {
                    "price": parsed.get("price"),
                    "last_alerted_price": deal.last_alerted_price,
                }
        except Exception as e:
            logger.error("DB get_alert_state error for %s: %s", url, e)
            return None

    def sweep_stale(self, source: str, max_age_days: int = 14) -> int:
        """Mark `available` rows of `source` not seen for `max_age_days` as `unavailable`.

        Deliberately age-based rather than set-based. Every scraper here samples a
        *window* rather than enumerating full inventory — PTT reads an Atom feed of
        recent posts, Shopee reads the newest ~180 search results — so "absent from
        this run" does not mean "delisted", it usually just means the listing scrolled
        out of the window. The previous set-based sweep marked 46 live Shopee listings
        unavailable in a single run because the search window had rotated completely.

        Genuine sold/delisted detection stays where it belongs: PTT's title keywords
        and Shopee's stock/grayout gatekeepers, which both inspect the listing itself.

        Returns the number of rows updated. Does not touch rows already 'sold' or
        'unavailable', and does not touch other sources.
        """
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max_age_days)
        try:
            with self.Session() as session:
                rows = (
                    session.query(Deal)
                    .filter(
                        Deal.source == source,
                        Deal.status == "available",
                        Deal.last_seen.isnot(None),
                        Deal.last_seen < cutoff,
                    )
                    .all()
                )
                for r in rows:
                    r.status = "unavailable"
                session.commit()
                return len(rows)
        except Exception as e:
            logger.error("DB sweep_stale error (source=%s): %s", source, e)
            return 0

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
                        # Callers scope work by source. Omitting it here is the
                        # same omission that once left the dashboard unable to
                        # tell where a listing came from.
                        "source": d.source,
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
                if model_type and item.get("series") not in _MODEL_TYPE_SERIES.get(model_type, ()):
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
                item["source"] = d.source
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
                    item["source"] = d.source
                    result.append(item)
                return result
        except Exception as e:
            logger.error("DB parsed read error: %s", e)
            return []


    def get_new_count(self) -> int:
        """Return count of deals first seen in the last 24 hours."""
        try:
            from datetime import timedelta
            with self.Session() as session:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                return session.query(Deal).filter(Deal.first_seen >= cutoff).count()
        except Exception as e:
            logger.error("DB get_new_count error: %s", e)
            return 0

    def get_last_seen(self) -> Optional[datetime]:
        """Return the most recent last_seen timestamp across all rows, or None if empty."""
        try:
            with self.Session() as session:
                result = session.execute(text("SELECT MAX(last_seen) FROM deals")).scalar()
                return result
        except Exception as e:
            logger.error("DB get_last_seen error: %s", e)
            return None


if __name__ == "__main__":
    db = DBManager()
    print("Database initialized.")
