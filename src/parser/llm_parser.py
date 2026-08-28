import json
import logging
import os
import re
import threading
import time
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.models.mac_spec import (
    VALID_RAM_GB,
    VALID_SSD_GB,
    MacBookSpec,
    ModelSeries,
)
from src.parser.text_extractor import (
    extract_location,
    extract_price,
    extract_spec_line,
    extract_warranty,
)

logger = logging.getLogger(__name__)

load_dotenv()

_model_id = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Built on first use, not at import. Constructing it eagerly meant the module
# could not be imported without a key — so even extract_specs_from_text, which
# is pure regex and never calls the API, was untestable and CI could not load it.
_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — required for LLM parsing, "
                "but not for the regex extractors in this module."
            )
        _client = genai.Client(api_key=key)
    return _client

# The free tier caps requests per minute, and the pipeline used to pace itself
# with a flat 1 s sleep in main.py — up to 60 calls a minute against a limit of
# 15. Throttling here rather than at the call site means no caller can forget.
# Gemini 3.5/3.1 Flash Lite allow 15 RPM and 500 requests a day; the non-Lite
# Flash models allow only 20 a day, so they are not a useful swap.
# Default sits below the free tier's 15 RPM on purpose: pacing exactly at
# the cap leaves no room for jitter or for the server counting a window
# slightly differently, and a 429 costs far more time than the headroom.
_RPM_LIMIT = max(1, int(os.getenv("GEMINI_RPM", "13")))
_MIN_CALL_INTERVAL = 60.0 / _RPM_LIMIT
_throttle_lock = threading.Lock()
_last_call_at = 0.0


class GeminiDailyQuotaExhausted(RuntimeError):
    """The 500-a-day free tier allowance is gone until midnight Pacific.

    Distinct from a per-minute 429 because the response is different: the
    per-minute limit is worth sleeping through, the daily one is not worth
    another call today. Callers stop asking and let the rest of the pipeline
    finish on what is already parsed.
    """


# The API returns 429 for both quotas. The body names which: per-day carries
# `PerDay` in its quotaId, per-minute carries `PerMinute`. Matching the id
# rather than the prose keeps this working when the message text is reworded.
_DAILY_QUOTA_MARKERS = (
    "generaterequestsperdayperprojectpermodel",
    "generate_content_free_tier_requests",
    "perdayperproject",
)


def _is_daily_quota_error(err: str) -> bool:
    low = err.lower()
    if "429" not in low and "resource_exhausted" not in low:
        return False
    # A per-minute rejection can mention the daily metric in passing; an
    # explicit PerMinute quotaId settles it in the other direction.
    if "perminute" in low.replace("_", ""):
        return False
    return any(m in low.replace("_", "").replace("-", "") or m in low
               for m in _DAILY_QUOTA_MARKERS)


def _throttle() -> None:
    """Block until the next call would stay inside the per-minute budget."""
    global _last_call_at
    with _throttle_lock:
        wait = _MIN_CALL_INTERVAL - (time.monotonic() - _last_call_at)
        if wait > 0:
            logger.debug("Throttling %.1fs to stay under %d RPM", wait, _RPM_LIMIT)
            time.sleep(wait)
        _last_call_at = time.monotonic()

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

# Configurations Apple has actually shipped. Anything outside these is a
# misread, not an exotic build — validating against them is what stops a GPU
# core count becoming a RAM size.
_VALID_RAM = set(VALID_RAM_GB)
_VALID_SSD = set(VALID_SSD_GB)

# "8C10G", "10C/10G" are CPU/GPU core counts. Left in place they read as
# 10 GB RAM and an 8 TB SSD — a real listing was stored that way, and the
# bogus SSD then earned a VFM bonus.
_CORE_COUNT_RE = re.compile(r'\d+\s*C\s*/?\s*\d+\s*G', re.I)

# "16G/512G", "8G+512G", "36G/2TB", "16GB 256GB"
_PAIR_RE = re.compile(
    r'\b(\d{1,3})\s*(?:G|GB)?\s*[/+\s]\s*(\d{1,4})\s*(G|GB|T|TB)?\b', re.I)

# G may be followed by Chinese ("16G記憶體"), so no trailing \b after the unit.
_RAM_RE = re.compile(r'\b(\d{1,3})\s*(?:GB|G)\s*(?:RAM|記憶體|統一記憶體|記憶)?', re.I)
_SSD_RE = re.compile(r'\b(\d{3,4})\s*(?:GB|G)\s*(?:SSD|硬碟|儲存)?', re.I)
_SSD_TB_RE = re.compile(r'\b([1248])\s*TB?\b(?!\w)', re.I)


def _as_ssd_gb(value: int, unit: str | None) -> int | None:
    """Normalise an SSD figure to GB. A bare 1/2/4/8 means terabytes."""
    if unit and unit.upper().startswith("T"):
        value *= 1024
    elif value <= 8:
        value *= 1024
    return value if value in _VALID_SSD else None


def extract_specs_from_text(text: str) -> tuple:
    """Pull (ram_gb, ssd_gb) out of a title or spec line, or (None, None).

    Returning nothing is fine — the LLM fills the gap, and a wrong number is
    far worse than a missing one because it feeds the VFM formula directly.
    """
    text = _CORE_COUNT_RE.sub(" ", preprocess_text(text))

    # A paired "RAM/SSD" reading is the most reliable, so try every pair in the
    # string and take the first that is a configuration Apple sells. The old
    # version stopped at the first regex hit, so "8C7G/8G/256G" gave up on the
    # core-count fragment and never reached the real 8G/256G behind it.
    for r_str, s_str, unit in _PAIR_RE.findall(text):
        ram = int(r_str)
        if ram not in _VALID_RAM:
            continue
        ssd = _as_ssd_gb(int(s_str), unit)
        if ssd is not None:
            return ram, ssd

    ram = ssd = None
    for m in _RAM_RE.finditer(text):
        if (v := int(m.group(1))) in _VALID_RAM:
            ram = v
            break
    for m in _SSD_RE.finditer(text):
        if (v := _as_ssd_gb(int(m.group(1)), None)) is not None:
            ssd = v
            break
    if ssd is None and (m := _SSD_TB_RE.search(text)):
        ssd = _as_ssd_gb(int(m.group(1)), "T")

    # 8 is a valid RAM size and a valid TB count; if the only number found is
    # the same one for both, we cannot tell which it was.
    if ram is not None and ssd == ram * 1024 and ram == 8:
        ssd = None
    return ram, ssd

def infer_correct_year(item: dict, title: str) -> tuple:
    original_year = item.get("release_year")
    series = item.get("series", "Air") if item.get("series") else "Air"
    chip = str(item.get("chip", "")).upper()
    title_lower = title.lower()
    inferred_year = original_year
    is_inferred = False
    # The A18 Pro shipped in exactly one Mac, so the chip alone fixes the year.
    if "a18" in chip.lower():
        inferred_year = 2026
    elif "m2" in chip.lower() and "air" in series.lower() and "15" in title_lower:
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
        max_retries = 3
        base_delay = 2
        response = None
        for attempt in range(max_retries):
            try:
                _throttle()
                response = _get_client().models.generate_content(
                    model=_model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                break
            except Exception as e:
                err_str = str(e)
                # A 429 is two different problems wearing one status code, and
                # only one of them is worth waiting out. The daily allowance
                # resets at midnight Pacific, so sleeping 65 s against it burns
                # the run for nothing: 17 retries cost 18 minutes of a task
                # capped at an hour, and the pipeline never reached scoring.
                if _is_daily_quota_error(err_str):
                    raise GeminiDailyQuotaExhausted(err_str) from e
                if attempt < max_retries - 1 and ("503" in err_str or "429" in err_str or "unavailable" in err_str.lower()):
                    # The per-minute quota, where the wait does clear the
                    # window — 2 s then 4 s just retried into the same
                    # rejection.
                    delay = 65 if "429" in err_str else base_delay
                    logger.info("Model busy, retrying in %ds... (Attempt %d/%d) %s",
                                delay, attempt + 1, max_retries, err_str)
                    time.sleep(delay)
                    base_delay *= 2
                else:
                    raise e

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
