"""Temp script: reset last_alerted_price on one deal to force a notifier alert."""
import json
import sys

from src.database.db_manager import DBManager, Deal


def main():
    db = DBManager()
    with db.Session() as session:
        deals = session.query(Deal).filter(Deal.parsed_json.isnot(None)).all()
        target = None
        for d in deals:
            try:
                parsed = json.loads(d.parsed_json)
                score = float(parsed.get("price") or 0)
                if score > 100:
                    target = d
                    break
            except (ValueError, TypeError):
                continue

        if target is None:
            print("No qualifying deal found.")
            sys.exit(1)

        target.last_alerted_price = 100
        session.commit()
        print(f"Updated: {target.title}")
        print(f"  URL: {target.url}")
        print("  last_alerted_price → 100")


if __name__ == "__main__":
    main()
