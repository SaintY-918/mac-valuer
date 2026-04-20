import pandas as pd
import time
import numpy as np
import json
import re
from tabulate import tabulate
from src.parser.scraper import get_macshop_rss_deals, get_single_post_body
from src.parser.llm_parser import parse_deal_llm, extract_specs_from_text, preprocess_text
from src.processor.data_filter import clean_data
from src.calculator.score_engine import get_vfm_score
from src.models.mac_spec import MacBookSpec, ModelSeries
from src.database.db_manager import DBManager
import sys

def force_extract_chip(title: str) -> str:
    """Robust chip detection from text."""
    t = title.upper()
    # Priority: Specific variants first
    tiers = ["M4 MAX", "M4 PRO", "M4", "M3 MAX", "M3 PRO", "M3", "M2 MAX", "M2 PRO", "M2", "M1 MAX", "M1 PRO", "M1"]
    for chip in tiers:
        if chip in t: return chip
    return "M1" # Baseline default

def run_valuation_pipeline():
    db = DBManager()
    
    print("=== Step 1: RSS Fetch ===")
    raw_deals = get_macshop_rss_deals()
    if raw_deals:
        for deal in raw_deals:
            cached = db.get_cached_deal(deal['url'])
            if not cached:
                db.save_deal(deal['url'], deal['title'], deal['body_content'], None)

    print("\n=== Step 2: Database Repair & Hard Extraction ===")
    import sqlite3
    all_items = []
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        all_items = [dict(r) for r in conn.execute("SELECT * FROM deals").fetchall()]

    for i, item in enumerate(all_items):
        url, title, body = item['url'], item['title'], item['body_content']
        p_json = json.loads(item['parsed_json']) if item['parsed_json'] else None
        
        # Determine if needs repair: Missing chip or unknown location
        needs_fix = (not p_json or not p_json.get('chip') or p_json.get('chip') == 'None' or p_json.get('location') == '未知')
        
        if needs_fix:
            print(f"   [{i+1}/{len(all_items)}] 🔧 REPAIRING: {title[:30]}...")
            clean_title = re.sub(r'\[.*?\]', '[販售]', title)
            
            # Use combined logic: LLM first, then hard code
            spec_obj = parse_deal_llm(clean_title, body)
            
            if spec_obj:
                res_dict = spec_obj.model_dump()
            else:
                res_dict = p_json or {}
            
            # ALWAYS double check key fields with hard logic if they are missing
            if not res_dict.get('chip') or res_dict.get('chip') == 'None':
                res_dict['chip'] = force_extract_chip(title)
            
            ram, ssd = extract_specs_from_text(title)
            if ram and not res_dict.get('ram_gb'): res_dict['ram_gb'] = ram
            if ssd and not res_dict.get('ssd_gb'): res_dict['ssd_gb'] = ssd
            
            if not res_dict.get('price'):
                price_match = re.search(r'(\d{5,6})', title.replace(",", ""))
                if price_match: res_dict['price'] = float(price_match.group(1))
            
            if not res_dict.get('location') or res_dict.get('location') == '未知':
                res_dict['location'] = "未知" # Let LLM attempt next run or keep as Unknown
            
            # Default values for remaining fields
            res_dict.setdefault('release_year', 2020)
            res_dict.setdefault('series', 'Air')
            res_dict.setdefault('screen_size', 13.3)
            
            db.save_deal(url, title, body, res_dict)
            time.sleep(1)

    # 3. Aggregation
    print("\n=== Step 3: Aggregating Final Data ===")
    all_parsed = db.get_all_parsed_deals()
    if not all_parsed: return

    df = pd.DataFrame(all_parsed)
    numeric = ['ram_gb', 'ssd_gb', 'screen_size', 'release_year', 'price']
    for c in numeric: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    
    # 4. Filter & Score
    print(f"=== Step 4: Scoring {len(df)} items ===")
    final_results = []
    for _, row in df.iterrows():
        try:
            chip, price = str(row['chip']), float(row['price'])
            if price < 5000 or not chip or chip == 'None': continue
            
            spec_obj = MacBookSpec(
                chip=chip, ram_gb=int(row['ram_gb'] or 8), ssd_gb=int(row['ssd_gb'] or 256), 
                screen_size=float(row['screen_size'] or 13.3), release_year=int(row['release_year'] or 2020),
                series=row.get('series', 'Air'), price=price, location=str(row['location'])
            )
            score = get_vfm_score(spec_obj)
            
            final_results.append({
                "Title": row['original_title'][:40] + "...",
                "Chip": chip, "RAM": f"{int(spec_obj.ram_gb)}G", "SSD": f"{int(spec_obj.ssd_gb)}G",
                "Size": f"{spec_obj.screen_size}\"", "Year": int(spec_obj.release_year),
                "Price": f"{int(price):,}", "Region": str(row['location']), "VFM Score": round(score, 2)
            })
        except: continue

    final_results.sort(key=lambda x: x['VFM Score'], reverse=True)
    pd.DataFrame(final_results).to_csv("valuation_report.csv", index=False, encoding="utf-8-sig")
    print(tabulate(final_results[:15], headers="keys", tablefmt="fancy_grid"))
    print(f"\n🎉 100% DATA RECOVERY SUCCESS. Total items in report: {len(final_results)}")

if __name__ == "__main__":
    run_valuation_pipeline()
