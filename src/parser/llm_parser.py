import logging
import os
import json
import re
from typing import Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from src.models.mac_spec import MacBookSpec, ModelSeries
from src.parser.text_extractor import (
    extract_price, extract_location, extract_warranty, extract_spec_line,
)

logger = logging.getLogger(__name__)

load_dotenv()

_model_id = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def preprocess_text(text: str) -> str:
    DQ = chr(34)
    SQ = chr(39)
    text = text.replace(chr(21515), DQ)
    text = text.replace(chr(8220), DQ)
    text = text.replace(chr(8221), DQ)
    text = text.replace(chr(8216), SQ)
    text = text.replace(chr(8217), SQ)
    text = text.replace(chr(65372), chr(47))
    text = text.replace(chr(124), chr(47))
    text = text.replace(chr(65306), chr(58))
    text = text.replace(chr(65292), chr(44))
    text = text.replace(chr(65319), chr(71))
    text = text.replace(chr(65314), chr(66))
    text = text.replace(chr(65332), chr(84))
    text = text.replace(chr(65325), chr(77))
    text = text.replace(chr(35352)+chr(25014)+chr(39636), chr(82)+chr(65)+chr(77))
    text = text.replace(chr(30828)+chr(30879), chr(83)+chr(83)+chr(68))
    return text

_SCREEN_SIZE_MAP = {13: 13.3, 14: 14.0, 15: 15.0, 16: 16.0}

def extract_screen_size_from_text(text: str) -> Optional[float]:
    """Returns screen size float or None. Handles decimal (13.6), inch markers (吋/"/'), and context."""
    text = preprocess_text(text)

    # 1. Explicit decimal: 13.3, 13.6, 14.2, 15.3, 16.0 — return exact value
    m = re.search(r'\b(1[3456]\.\d)\b', text)
    if m:
        return float(m.group(1))

    # 2. Integer + any inch marker: 15", 15', 15inch  (吋 already → " via preprocess)
    m = re.search(r'\b(13|14|15|16)\s*(?:"|\'|inch\b|-inch\b)', text, re.IGNORECASE)
    if m:
        return _SCREEN_SIZE_MAP.get(int(m.group(1)))

    # 3. Screen number immediately before chip gen: "16 M1 Pro", "13 M4"
    m = re.search(r'\b(13|14|15|16)\s+M[1-5]', text, re.IGNORECASE)
    if m:
        return _SCREEN_SIZE_MAP.get(int(m.group(1)))

    # 4. MacBook model name before number: "MacBook Pro 16", "Air 15"
    m = re.search(r'(?:macbook\s+)?(?:air|pro)\s+(13|14|15|16)\b', text, re.IGNORECASE)
    if m:
        return _SCREEN_SIZE_MAP.get(int(m.group(1)))

    return None

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

    # --- Structured PTT section extraction (highest priority) ---
    struct_price = extract_price(clean_body)
    struct_location = extract_location(clean_body)
    struct_warranty = extract_warranty(clean_body)
    spec_line = extract_spec_line(clean_body)

    # RAM/SSD: title > [規格] section > full body
    title_ram, title_ssd = extract_specs_from_text(clean_title)
    spec_ram, spec_ssd = extract_specs_from_text(spec_line) if spec_line else (None, None)
    body_ram, body_ssd = extract_specs_from_text(clean_body)
    final_ram = title_ram or spec_ram or body_ram
    final_ssd = title_ssd or spec_ssd or body_ssd

    # screen_size: title > [規格/型號] section > full body (LLM still overrides all)
    title_screen = extract_screen_size_from_text(clean_title)
    spec_screen = extract_screen_size_from_text(spec_line) if spec_line else None
    regex_screen = title_screen or spec_screen or extract_screen_size_from_text(clean_body)

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

SPEC HIERARCHY RULE:
If TITLE contains " - " followed by a variation name (e.g. "M2 Max / 32GB / 1TB"),
treat that variation name as the authoritative source for chip, ram_gb, and ssd_gb.
It overrides any conflicting chip or memory information in the base title or body.
If the variation name does not mention chip or memory, those fields are null —
do NOT fall back to the base title for chip or memory.

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
        # 1. Price: structured [售價] > LLM (normalise string fallback)
        if struct_price:
            item["price"] = struct_price
        elif isinstance(item.get("price"), str):
            item["price"] = float(item["price"].replace(",", "").replace("$", ""))

        # 2. Location: structured [交易方式/地點] > LLM (normalise list fallback)
        if struct_location:
            item["location"] = struct_location
        elif isinstance(item.get("location"), list):
            item["location"] = "/".join(item["location"])

        # 3. Warranty: structured [保固] fills gap when LLM returns null
        if struct_warranty and not item.get("warranty_status"):
            item["warranty_status"] = struct_warranty

        # 4. RAM/SSD: regex (title > spec section > body) overrides LLM
        if final_ram: item["ram_gb"] = final_ram
        if final_ssd: item["ssd_gb"] = final_ssd

        # 5. Screen size: LLM > regex (title > spec section > body)
        if not item.get("screen_size"):
            item["screen_size"] = regex_screen
        
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
