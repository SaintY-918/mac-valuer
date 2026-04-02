import urllib.parse
import re
import time
from playwright.sync_api import sync_playwright
from typing import List, Dict

def get_post_content(page, url: str) -> str:
    """Clicks into a post and extracts snippets. Returns empty if already sold."""
    try:
        page.goto(url, wait_until="domcontentloaded")
        content_element = page.query_selector("#main-content")
        if not content_element: return ""
        
        full_text = content_element.inner_text()
        
        # Case-insensitive "Sold" check
        text_start = full_text.strip().lower()
        if text_start.startswith("已售出") or "【已售出】" in text_start[:50] or "已售" in text_start[:20]:
            return "IGNORE_SOLD"
        
        snippets = []
        for tag in ["[規格]", "[售價]", "[價格]"]:
            idx = full_text.find(tag)
            if idx != -1:
                snippets.append(full_text[idx:idx+350])
        
        return "\n---\n".join(snippets)
    except Exception as e:
        print(f"   [Scraper] Failed to get content for {url}: {e}")
        return ""

def get_macshop_deals(pages_to_scrape: int = 10) -> List[Dict[str, str]]:
    """
    Scrapes 10 pages and excludes SOLD items early. Case-insensitive.
    """
    base_url = "https://www.ptt.cc"
    search_query = "[販售] MacBook"
    encoded_query = urllib.parse.quote(search_query)
    start_url = f"{base_url}/bbs/MacShop/search?q={encoded_query}"
    
    deals = []
    m_chips = ["m1", "m2", "m3", "m4"]
    sold_keywords = ["(售出)", "(已售出)", "[售出]", "已售", "售完", "售出"]
    
    with sync_playwright() as p:
        print(f"🚀 Starting Deep Scraper for {pages_to_scrape} pages (Case-Insensitive)...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        current_url = start_url
        for i in range(pages_to_scrape):
            try:
                print(f"📄 Scraping Page {i+1}...")
                page.goto(current_url, wait_until="domcontentloaded")
                
                if "over18" in page.url:
                    page.click("button[name='yes']")
                
                title_elements = page.query_selector_all("div.title a")
                if not title_elements: break
                
                page_meta = []
                for el in title_elements:
                    text = el.inner_text().strip()
                    text_lower = text.lower() # Normalize to lower case
                    url = base_url + el.get_attribute("href")
                    
                    # 1. Filter for M-chip (Case-insensitive)
                    is_m_chip = any(chip in text_lower for chip in m_chips)
                    # 2. Exclude sold items
                    is_sold_title = any(kw in text_lower for kw in sold_keywords)
                    # 3. Exclude noise
                    is_noise = any(kw in text_lower for kw in ["收購", "徵求", "配件", "iphone", "ipad", "watch"])
                    
                    if is_m_chip and not is_sold_title and not is_noise:
                        page_meta.append({"url": url, "title": text})
                
                for item in page_meta:
                    body = get_post_content(page, item["url"])
                    if body == "IGNORE_SOLD" or not body:
                        continue
                    item["body_content"] = body
                    deals.append(item)
                    time.sleep(0.1)
                
                prev_btn = page.query_selector("a.btn.wide:has-text('‹ 上頁')")
                if prev_btn:
                    current_url = base_url + prev_btn.get_attribute("href")
                else: break
                    
            except Exception as e:
                print(f"❌ Error on page {i+1}: {e}")
                break
                
        browser.close()
        
    print(f"\n✅ Finished: Extracted {len(deals)} active sales.")
    return deals
