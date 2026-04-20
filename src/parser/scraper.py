import feedparser
import re
import time
from playwright.sync_api import sync_playwright
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor

def get_single_post_body(url: str) -> str:
    """Fetches the FULL body content of a PTT post."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            content_element = page.query_selector("#main-content")
            if not content_element: return ""
            
            full_text = content_element.inner_text()
            
            # Simple cleanup to remove signature and standard PTT footers
            if "--" in full_text:
                full_text = full_text.split("--")[0]
            
            browser.close()
            return full_text
        except Exception:
            browser.close()
            return ""

def get_macshop_rss_deals() -> List[Dict[str, str]]:
    rss_url = "https://www.ptt.cc/atom/MacShop.xml"
    feed = feedparser.parse(rss_url)
    
    deals = []
    m_chips = ["m1", "m2", "m3", "m4"]
    exclude_titles = ["[徵]", "[交換]", "intel", "i5", "i7", "i9", "2017", "2018"]
    
    print(f"🚀 Fetching RSS from PTT MacShop... ({len(feed.entries)} entries found)")
    
    candidate_meta = []
    for entry in feed.entries:
        title = entry.title
        url = entry.link
        title_lower = title.lower()
        
        if any(tag in title for tag in exclude_titles): continue
        if not any(chip in title_lower for chip in m_chips): continue
        if "macbook" not in title_lower: continue
        
        candidate_meta.append({"url": url, "title": title})

    print(f"   ↳ {len(candidate_meta)} candidates pass RSS filter. Fetching FULL content...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        urls = [item['url'] for item in candidate_meta]
        results = list(executor.map(get_single_post_body, urls))
        
    for i, body in enumerate(results):
        if body and "售出" not in body[:100]:
            candidate_meta[i]["body_content"] = body
            deals.append(candidate_meta[i])
            
    print(f"✅ RSS Upgrade Finished: {len(deals)} valid sales extracted.")
    return deals
