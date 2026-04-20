import logging
import os
import json
import re
from typing import Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from src.models.mac_spec import MacBookSpec, ModelSeries

logger = logging.getLogger(__name__)

load_dotenv()

_model_id = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def preprocess_text(text: str) -> str:
    """Normalizes characters and handles common PTT separators."""
    replacements = {
        '吋': '"', '”': '"', '’': "'", '｜': '/', '|': '/',
        '：': ':', '，': ',', 'Ｇ': 'G', 'Ｂ': 'B', 'Ｔ': 'T', 'Ｍ': 'M',
        '記憶體': 'RAM', '硬碟': 'SSD'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def extract_specs_from_text(text: str) -> tuple:
    """Priority 1: Slash patterns (e.g., 16/512). Returns (ram, ssd)."""
    text = preprocess_text(text)
    matches = re.findall(r'(\d+)(?:G|GB|gb)?\s*/\s*(\d+)(?:G|GB|gb|T|TB|t|tb)?', text)
    
    if not matches:
        ram_match = re.search(r'\b(8|16|18|24|32|36|48|64|96|128)\s*(?:G|GB|gb|RAM|記憶體)\b', text, re.I)
        ssd_match = re.search(r'\b(128|256|512|1024|2048)\s*(?:G|GB|gb|SSD|硬碟)\b', text, re.I)
        if not ssd_match:
            ssd_match = re.search(r'\b(1|2|4|8)\s*(?:T|TB|tb)\b', text, re.I)
        
        ram = int(ram_match.group(1)) if ram_match else None
        ssd = None
        if ssd_match:
            val = int(ssd_match.group(1))
            ssd = val * 1024 if val < 10 else val
        return ram, ssd

    for r_str, s_str in matches:
        r, s = int(r_str), int(s_str)
        if 8 <= r <= 128 and (s >= 128 or s <= 8):
            ssd = s * 1024 if s <= 8 else s
            return r, ssd
            
    return None, None

def infer_correct_year(item: dict, title: str) -> tuple:
    original_year = item.get("release_year")
    series = item.get("series", "Air") if item.get("series") else "Air"
    chip = str(item.get("chip", "")).upper()
    title_lower = title.lower()
    inferred_year = original_year
    is_inferred = False
    if "m2" in chip.lower() and "air" in series.lower() and "15" in title_lower:
        inferred_year = 2023
    elif "pro" in series.lower():
        if "m3" in chip.lower(): inferred_year = 2023
        elif "m4" in chip.lower(): inferred_year = 2024
        elif "m5" in chip.lower(): inferred_year = 2025
        elif "m1" in chip.lower(): inferred_year = 2021
        elif "m2" in chip.lower(): inferred_year = 2023
    elif "air" in series.lower():
        if "m3" in chip.lower(): inferred_year = 2024
        elif "m4" in chip.lower(): inferred_year = 2025
        elif "m5" in chip.lower(): inferred_year = 2026
        elif "m1" in chip.lower(): inferred_year = 2020
        elif "m2" in chip.lower(): inferred_year = 2022
    if not original_year or original_year < 2015:
        is_inferred = True
    elif inferred_year and original_year != inferred_year:
        is_inferred = True
    return inferred_year or original_year, is_inferred

def parse_deal_llm(title: str, body_content: str) -> Optional[MacBookSpec]:
    clean_title = preprocess_text(title)
    clean_body = preprocess_text(body_content)
    title_ram, title_ssd = extract_specs_from_text(clean_title)
    body_ram, body_ssd = extract_specs_from_text(clean_body)
    final_ram = title_ram or body_ram
    final_ssd = title_ssd or body_ssd

    prompt = f"""You are an expert at parsing Taiwanese PTT MacBook second-hand listings.
Extract the following fields and return ONLY a JSON object matching this exact schema.
If a field cannot be determined with confidence, set it to null — never guess.

SCHEMA:
{{
  "chip":            "M1" | "M1 Pro" | "M1 Max" | "M2" | "M2 Pro" | "M2 Max" | "M3" | "M3 Pro" | "M3 Max" | "M4" | "M4 Pro" | "M4 Max" | null,
  "ram_gb":          <integer, e.g. 8 / 16 / 24 / 32> | null,
  "ssd_gb":          <integer in GB; 1TB = 1024, 2TB = 2048> | null,
  "screen_size":     <float, e.g. 13.3 / 14.0 / 15.0 / 16.0> | null,
  "release_year":    <4-digit integer> | null,
  "series":          "Air" | "Pro 13" | "Pro 14/16" | null,
  "price":           <integer, no commas; look for [售價] tag> | null,
  "location":        <single string with "/" separator, e.g. "台北/新竹"> | null,
  "battery_health":  <integer 0-100, e.g. 89; only if explicitly stated> | null,
  "warranty_status": <string, e.g. "2025-12" or "已過保" or "AppleCare+"; only if explicit> | null,
  "condition":       <string, e.g. "全新未拆" / "九成新" / "輕微使用痕跡" / "明顯使用痕跡"; only if explicit> | null
}}

RULES:
- price: must be a plain integer (no commas, no $ sign). Look for [售價] tag first.
- ssd_gb: convert TB to GB (1T = 1024, 2T = 2048).
- location: join multiple cities with "/" into one string.
- battery_health / warranty_status / condition: extract ONLY when the seller explicitly states them. Otherwise null.

POST DATA:
TITLE: {clean_title}
BODY:
{clean_body}
"""
    try:
        response = _client.models.generate_content(
            model=_model_id,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if not json_match: return None
        item = json.loads(json_match.group(0))
        if item.get("ignore"): return None
        
        # --- DATA CLEANUP ---
        # 1. Price cleanup (remove commas if AI returned string)
        if isinstance(item.get("price"), str):
            item["price"] = float(item["price"].replace(",", "").replace("$", ""))
            
        # 2. Location cleanup (convert list to string)
        if isinstance(item.get("location"), list):
            item["location"] = "/".join(item["location"])
        
        # 3. Specs priority
        if final_ram: item["ram_gb"] = final_ram
        if final_ssd: item["ssd_gb"] = final_ssd
        
        is_spec_inferred = (not item.get("ram_gb") or not item.get("ssd_gb"))
        correct_year, was_inferred = infer_correct_year(item, clean_title)
        item["release_year"] = correct_year
        item["is_year_inferred"] = was_inferred
        item["is_spec_inferred"] = is_spec_inferred
        
        if item.get("series") not in [s.value for s in ModelSeries]:
            item["series"] = "Air" if "air" in clean_title.lower() else "Pro 13"
            
        return MacBookSpec(**item)
    except Exception as e:
        logger.warning("LLM parser error: %s", e)
        return None
