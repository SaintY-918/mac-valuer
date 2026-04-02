import sqlite3
import json
import os
from typing import Optional, Dict

class DBManager:
    def __init__(self, db_path="mac_deals.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        # Ensure directory exists if needed, but here it's project root
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deals (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    body_content TEXT,
                    parsed_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def get_cached_deal(self, url: str) -> Optional[Dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT * FROM deals WHERE url = ?", (url,))
                row = cur.fetchone()
                if row:
                    return {
                        "title": row["title"],
                        "body_content": row["body_content"],
                        "parsed_json": json.loads(row["parsed_json"]) if row["parsed_json"] else None
                    }
        except Exception as e:
            print(f"DB Read Error: {e}")
        return None

    def save_deal(self, url: str, title: str, body_content: str, parsed_json: Optional[Dict] = None):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO deals (url, title, body_content, parsed_json)
                    VALUES (?, ?, ?, ?)
                """, (url, title, body_content, json.dumps(parsed_json) if parsed_json else None))
        except Exception as e:
            print(f"DB Write Error: {e}")

if __name__ == "__main__":
    db = DBManager()
    print("Database initialized.")
