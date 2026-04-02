import pandas as pd
from tabulate import tabulate
from src.parser.scraper import get_macshop_deals
from src.parser.llm_parser import parse_deal_llm, preprocess_text
from src.processor.data_filter import clean_data
from src.calculator.score_engine import get_vfm_score
from src.models.mac_spec import MacBookSpec
from src.database.db_manager import DBManager
import sys

# --- CONFIG ---
USE_MOCK = False 

def infer_specs_from_chip(chip: str, series: str) -> tuple:
    """Fallback inference: ram, ssd, screen_size."""
    chip = str(chip).lower()
    series = str(series).lower()
    
    ram, ssd, size = 16, 512, 14.0
    if "m3" in chip and "pro" in chip: ram, ssd, size = 18, 512, 14.2
    elif "air" in series:
        size = 13.6 if any(x in chip for x in ["m2", "m3", "m4"]) else 13.3
        ram, ssd = 8, 256
    elif "pro" in series:
        size = 14.2 if "14" in series or any(x in chip for x in ["m1 pro", "m2 pro", "m3 pro", "m4 pro"]) else 13.3
        
    return ram, ssd, size

def run_valuation_pipeline():
    db = DBManager()
    
    print("=== Step 1: Scraping PTT MacShop SEARCH Results (10 Pages) ===")
    raw_deals = get_macshop_deals(pages_to_scrape=10)
    
    if not raw_deals:
        print("⚠️  No active deals found.")
        sys.exit(0)

    print("\n=== Step 2: Processing Content & LLM Parsing (with Smart Cache Refresh) ===")
    final_specs_list = []
    
    for i, deal in enumerate(raw_deals):
        url = deal['url']
        title = deal['title']
        
        cached = db.get_cached_deal(url)
        spec_dict = None
        
        if cached and cached.get('parsed_json'):
            temp_dict = cached['parsed_json']
            # FORCE REFRESH if cache is from old data model (missing screen_size)
            if 'screen_size' in temp_dict and temp_dict['screen_size'] is not None:
                print(f"   [{i+1}/{len(raw_deals)}] ⚡ CACHE HIT: {title[:40]}...")
                spec_dict = temp_dict
            else:
                print(f"   [{i+1}/{len(raw_deals)}] 🔄 OLD CACHE DETECTED, RE-PARSING: {title[:40]}...")

        if not spec_dict:
            print(f"   [{i+1}/{len(raw_deals)}] 🤖 LLM PARSING: {title[:40]}...")
            spec_obj = parse_deal_llm(title, deal['body_content'])
            if spec_obj:
                spec_dict = spec_obj.model_dump()
                db.save_deal(url, title, deal['body_content'], spec_dict)
            else:
                db.save_deal(url, title, deal['body_content'], None)

        if spec_dict:
            spec_dict['original_title'] = title
            final_specs_list.append(spec_dict)

    if not final_specs_list:
        print("❌ No valid MacBook data after parsing.")
        return

    df = pd.DataFrame(final_specs_list)
    print(f"\n=== Step 3: Filtering {len(df)} parsed items ===")
    cleaned_df = clean_data(df)
    print(f"🛡️  Cleaning: {len(df) - len(cleaned_df)} removed, {len(cleaned_df)} remaining.")

    print("\n=== Step 4: Value-for-Money Score Calculation ===")
    final_results = []
    for _, row in cleaned_df.iterrows():
        try:
            ram = row.get('ram_gb')
            ssd = row.get('ssd_gb')
            size = row.get('screen_size')
            is_spec_inferred = row.get('is_spec_inferred', False)
            
            # Grannular Inference
            ram_inf, ssd_inf, size_inf = infer_specs_from_chip(row['chip'], str(row['series']))
            if not ram: 
                ram = ram_inf
                is_spec_inferred = True
            if not ssd: 
                ssd = ssd_inf
                is_spec_inferred = True
            if not size: 
                size = size_inf

            spec_obj = MacBookSpec(
                chip=row['chip'], ram_gb=ram, ssd_gb=ssd, screen_size=size,
                release_year=row['release_year'], series=row['series'], price=row['price'],
                is_year_inferred=row.get('is_year_inferred', False), is_spec_inferred=is_spec_inferred
            )
            vfm_score = get_vfm_score(spec_obj)
            
            final_results.append({
                "Title": row['original_title'],
                "Chip": row['chip'],
                "RAM": f"{int(ram)}G" + ("🔍" if is_spec_inferred else ""),
                "SSD": f"{int(ssd)}G",
                "Size": f"{size}\"",
                "Year": row['release_year'],
                "Price": f"{int(row['price']):,}",
                "VFM Score": round(vfm_score, 2),
                "is_year_inferred": row.get('is_year_inferred', False)
            })
        except Exception: continue

    if not final_results: return

    final_results.sort(key=lambda x: x['VFM Score'], reverse=True)
    try:
        pd.DataFrame(final_results).to_csv("valuation_report.csv", index=False, encoding="utf-8-sig")
    except PermissionError: pass
    
    print("\n=== FINAL VALUATION RESULTS ===")
    print(tabulate(final_results, headers="keys", tablefmt="fancy_grid"))
    print(f"\n🎉 Analysis complete. Found {len(final_results)} active items.")

if __name__ == "__main__":
    run_valuation_pipeline()
