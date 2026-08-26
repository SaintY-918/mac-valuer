"""Report which database DATABASE_URL actually points at, and what is in it.

    python -m src.scripts.check_db

Use this before wiring up the local scheduled Shopee run: if it says SQLite,
the scrape lands in a local file and never reaches the cloud Dashboard.
Passwords are masked, so the output is safe to paste when asking for help.
"""

import os
import re
import sys

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from src.database.db_manager import DBManager  # noqa: E402


def mask(url: str) -> str:
    return re.sub(r"(://[^:/@]+):[^@]*@", r"\1:***@", url)


def main() -> int:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set (neither in .env nor the environment).")
        return 2

    print(f"DATABASE_URL : {mask(url)}")

    if url.startswith("sqlite"):
        kind, is_neon = "SQLite (本機檔案)", False
    elif "neon.tech" in url:
        kind, is_neon = "PostgreSQL @ Neon (雲端)", True
    else:
        kind, is_neon = "PostgreSQL (非 Neon 主機)", False
    print(f"目標           : {kind}")

    if is_neon and "sslmode=require" not in url:
        print("警告           : Neon 連線字串缺少 ?sslmode=require")
    if url.startswith("postgresql://"):
        print("警告           : 請改用 postgresql+psycopg2:// 前綴（本專案用 psycopg2）")

    # Query the source column directly: get_filtered_deals() filters on source in
    # SQL but does not copy it into the returned dict, so counting from there
    # would report every row as unknown.
    try:
        db = DBManager()
        with db.Session() as session:
            rows = session.execute(text(
                "SELECT COALESCE(source, '(未標記)'), status, COUNT(*), MAX(last_seen) "
                "FROM deals GROUP BY source, status ORDER BY 1, 2"
            )).all()
            total = session.execute(text("SELECT COUNT(*) FROM deals")).scalar()
    except Exception as e:
        print(f"\n連線失敗       : {type(e).__name__}: {e}")
        return 1

    print(f"\n連線成功，deals 共 {total} 筆")
    print(f"\n  {'來源':<12} {'狀態':<14} {'筆數':>6}   最後出現 (UTC)")
    for source, status, n, last_seen in rows:
        print(f"  {source:<12} {status:<14} {n:>6}   {last_seen}")

    if not is_neon:
        print("\n=> 這不是 Neon。本機排程跑出來的資料不會出現在雲端 Dashboard。")
        print("   到 https://console.neon.tech 取得連線字串後改寫 .env 的 DATABASE_URL。")
    else:
        print("\n=> 已指向 Neon，本機排程的結果會進到雲端 Dashboard。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
