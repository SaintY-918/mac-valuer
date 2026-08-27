import asyncio
import json
import logging
import os
import random
import re
import urllib.parse
from pathlib import Path

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    AsyncCamoufox = None  # type: ignore

from playwright.async_api import BrowserContext, Page

from src.scrapers.base import BaseScraper, RawListing
from src.scrapers.shopee_api import ShopeeAffiliateScraper, credentials_configured

logger = logging.getLogger(__name__)


class ShopeeSessionExpired(RuntimeError):
    """Headless run hit the login / anti-bot wall with no usable session.

    Raised rather than returning [] so the pipeline reports a hard failure —
    a silent empty list is indistinguishable from 'no new listings today'.
    """


_EXCLUDE_TITLES = ["殼", "膜", "零件機", "報廢", "充電線", "保護貼", "貼膜", "支架", "轉接"]
_ITEM_URL_RE = re.compile(r"/product/(\d+)/(\d+)")

# L1 gatekeeper bounds, named once so the candidate filter and the per-item
# builder cannot drift to different numbers.
L1_MIN_PRICE = 5000
L1_MAX_PRICE = 150000


def _extract_model_price(model: dict) -> int:
    """Return the real checkout price (lowest priority: top-level `price` which
    equals price_before_discount when a discount is active)."""
    pi = model.get("price_info") or {}
    # current_price is what the buyer actually pays
    for key in ("current_price", "discounted_price"):
        v = pi.get(key)
        if v and v > 0:
            return int(v)
    # Fallback: top-level price — only correct when there is NO discount
    return int(model.get("price") or 0)


class ShopeeScraper(BaseScraper):
    def __init__(self):
        self._delay_min = float(os.getenv("SHOPEE_MIN_DELAY", "3"))
        self._delay_max = float(os.getenv("SHOPEE_MAX_DELAY", "8"))
        self._max_calls = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "100"))
        self._concurrency = int(os.getenv("SHOPEE_CONCURRENCY", "1"))
        self._sem = asyncio.Semaphore(self._concurrency)
        self._state_path = Path(os.getenv("SHOPEE_STATE_PATH", "shopee_state.json"))
        self._headless = os.getenv("SHOPEE_HEADLESS", "false").lower() == "true"
        # Visiting a detail page per candidate meant ~33 navigations per run in
        # one session — the pattern that gets a client classified as a crawler.
        # The search response already carries name and price (the L1 filter runs
        # on them), so skipping details costs description and per-variant pricing
        # but cuts the request count to the three search pages.
        self._skip_details = os.getenv("SHOPEE_SKIP_DETAILS", "true").lower() == "true"
        self._current_calls = 0

    # ------------------------------------------------------------------
    # Human-behaviour helpers
    # ------------------------------------------------------------------

    async def _human_delay(self):
        await asyncio.sleep(random.uniform(self._delay_min, self._delay_max))

    async def _human_interact(self, page: Page):
        await page.mouse.move(random.randint(200, 1080), random.randint(100, 600))
        await page.mouse.wheel(0, random.randint(200, 600))

    # ------------------------------------------------------------------
    # Session persistence helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> dict | None:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Failed to load session state: %s", e)
        else:
            logger.warning("Session state not found at %s — starting fresh", self._state_path)
        return None

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    async def fetch_detail(self, _url: str) -> str:
        return ""

    async def fetch_listings(self) -> list[RawListing]:
        """Dispatch to the official affiliate API when credentials are present,
        otherwise fall back to the browser scraper.

        The switch lives here rather than in the pipeline so main.py stays free
        of platform branching (.spec/specs/scraper/spec.md — Strategy Pattern).
        """
        if credentials_configured():
            logger.info("Shopee: using Affiliate Open API (SHOPEE_APP_ID is set)")
            return await ShopeeAffiliateScraper().fetch_listings()

        logger.info("Shopee: no affiliate credentials — falling back to browser scraper")
        return await self._fetch_listings_browser()

    async def _fetch_listings_browser(self) -> list[RawListing]:
        if AsyncCamoufox is None:
            raise RuntimeError(
                "camoufox is not installed. Run: pip install camoufox && python -m camoufox fetch"
            )

        results: list[RawListing] = []
        self._current_calls = 0

        storage_state = self._load_state()

        async with AsyncCamoufox(
            headless=self._headless,
            os="windows",
        ) as browser:
            ctx_kwargs: dict = {"locale": "zh-TW", "timezone_id": "Asia/Taipei"}
            if storage_state:
                ctx_kwargs["storage_state"] = storage_state

            context: BrowserContext = await browser.new_context(**ctx_kwargs)

            try:
                page = await context.new_page()

                # Page 1: initial fetch + login check
                items_p1 = await self._search_items(page, newest=0)

                if "login" in page.url or not items_p1:
                    if self._headless:
                        raise ShopeeSessionExpired(
                            f"Shopee login / anti-bot wall hit at {page.url} with SHOPEE_HEADLESS=true. "
                            f"Session at {self._state_path} is missing or expired — "
                            "re-run once with SHOPEE_HEADLESS=false to log in, "
                            "or configure SHOPEE_APP_ID / SHOPEE_APP_SECRET to use the affiliate API."
                        )

                    print("\n=======================================================")
                    print("[ShopeeScraper] Login / anti-bot wall triggered!")
                    print(f"Current URL: {page.url}")
                    print("Please log in manually in the browser, then press Enter.")
                    print("=======================================================")
                    try:
                        await asyncio.to_thread(input, "Press Enter to continue: ")
                    except EOFError:
                        logger.warning("Non-interactive terminal — waiting 60 s for login")
                        await asyncio.sleep(60)
                    items_p1 = await self._search_items(page, newest=0)

                # Pages 2–3: same session, sequential navigation
                all_items: list[dict] = list(items_p1)
                for newest in (60, 120):
                    await asyncio.sleep(random.uniform(3, 7))
                    page_items = await self._search_items(page, newest=newest)
                    all_items.extend(page_items)

                await page.close()

                # Persist session for next cron run
                await context.storage_state(path=str(self._state_path))
                logger.info("Session state saved to %s", self._state_path)

                # Dedup by (shopid, itemid) across pages
                seen: set[tuple] = set()
                unique_items: list[dict] = []
                for item in all_items:
                    key = (item.get("shopid"), item.get("itemid"))
                    if key not in seen:
                        seen.add(key)
                        unique_items.append(item)
                logger.info("Pagination: %d total items, %d unique after dedup", len(all_items), len(unique_items))

                # L1 Gatekeeper: price range + title exclusion
                candidates = [
                    item for item in unique_items
                    if L1_MIN_PRICE <= item.get("price", 0) / 100000 <= L1_MAX_PRICE
                    and not any(w in item.get("name", "") for w in _EXCLUDE_TITLES)
                ]
                logger.info("L1 filter: %d / %d items passed", len(candidates), len(unique_items))

                if self._skip_details:
                    logger.info("SHOPEE_SKIP_DETAILS=true — building listings from search "
                                "results only (no per-item page visits)")

                shop_listing_count: dict[int, int] = {}
                for item in candidates:
                    if self._current_calls >= self._max_calls:
                        logger.warning("MAX_LLM_CALLS_PER_RUN (%d) reached", self._max_calls)
                        break
                    shop_id = item.get("shopid")
                    if shop_id and shop_listing_count.get(shop_id, 0) >= 3:
                        logger.debug("Diversity Guard: shop %s capped, skipping item %s", shop_id, item.get("itemid"))
                        continue
                    if self._skip_details:
                        lst = self._listing_from_search_item(item)
                        item_listings = [lst] if lst else []
                    else:
                        item_listings = await self._fetch_item_details(context, item)
                    for lst in item_listings:
                        results.append(lst)
                        self._current_calls += 1
                        if self._current_calls >= self._max_calls:
                            break
                    if shop_id:
                        shop_listing_count[shop_id] = shop_listing_count.get(shop_id, 0) + len(item_listings)

            except ShopeeSessionExpired:
                raise  # let the pipeline report a hard failure, not an empty run
            except Exception as e:
                logger.error("fetch_listings error: %s", e)
            finally:
                await context.close()

        return results

    # ------------------------------------------------------------------
    # Search page: response interception + DOM fallback
    # ------------------------------------------------------------------

    async def _search_items(self, page: Page, newest: int = 0) -> list[dict]:
        intercepted: list[dict] = []

        async def handle_response(response):
            if "api/v4/search/search_items" in response.url:
                try:
                    data = await response.json()
                    if "items" in data:
                        intercepted.extend(data["items"])
                        logger.info("Intercepted %d search items", len(data["items"]))
                except Exception:
                    pass

        page.on("response", handle_response)

        url = "https://shopee.tw/search?keyword=" + urllib.parse.quote("二手 MacBook") + f"&newest={newest}"
        logger.info("Navigating to: %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)  # allow background API responses to fire
        try:
            await self._human_interact(page)
        except Exception:
            pass
        await self._human_delay()

        if "login" in page.url:
            page.remove_listener("response", handle_response)
            return []

        # Dismiss language popup if present
        try:
            btn = await page.wait_for_selector('text="繁體中文"', timeout=3000)
            if btn:
                await btn.click()
                await page.wait_for_load_state("networkidle")
                await self._human_delay()
        except Exception:
            pass

        try:
            await self._human_interact(page)
        except Exception:
            pass
        await self._human_delay()
        page.remove_listener("response", handle_response)

        # Normalise item_basic wrapper
        clean = [i["item_basic"] if "item_basic" in i else i for i in intercepted]

        if not clean:
            logger.warning("No items intercepted — falling back to DOM scrape")
            clean = await self._dom_fallback(page)

        return clean

    async def _dom_fallback(self, page: Page) -> list[dict]:
        try:
            await page.wait_for_selector("a[href*='/product/']", timeout=8000)
        except Exception:
            logger.warning("DOM fallback: no product links found within timeout")
            return []

        anchors = await page.query_selector_all("a[href*='/product/']")
        seen: set[str] = set()
        items: list[dict] = []

        for a in anchors:
            href = await a.get_attribute("href") or ""
            m = _ITEM_URL_RE.search(href)
            if not m:
                continue
            shop_id, item_id = m.group(1), m.group(2)
            key = f"{shop_id}_{item_id}"
            if key in seen:
                continue
            seen.add(key)
            title = (await a.inner_text()).strip()[:120] or href
            items.append({"shopid": int(shop_id), "itemid": int(item_id), "name": title, "price": 0})

        logger.info("DOM fallback yielded %d product stubs", len(items))
        return items

    # ------------------------------------------------------------------
    # Lite path: build a listing from the search result alone
    # ------------------------------------------------------------------

    def _listing_from_search_item(self, item: dict) -> RawListing | None:
        """One listing per product, using only what the search response carried.

        No page visit, so no description and no per-variant prices — Variant
        Flattening does not apply here. Shopee titles carry most of the spec
        ("Macbook Air 15 2025 M4 10C10G/16G/256G"), which is what the LLM
        parses anyway.
        """
        shop_id, item_id = item.get("shopid"), item.get("itemid")
        name = (item.get("name") or "").strip()
        if not shop_id or not item_id or not name:
            return None

        # Search prices are in micro-units, as the L1 filter above assumes.
        price = int(item.get("price", 0)) / 100000
        # L1: the caller already applies this range, but enforcing it here too
        # means the method cannot emit an out-of-range listing if it is ever
        # called from somewhere else — which is how the Carousell builder works.
        if not (L1_MIN_PRICE <= price <= L1_MAX_PRICE):
            return None

        # L2: the search feed keeps sold-out items listed, so drop them when it
        # tells us the stock. Absent field means unknown — keep it.
        stock = item.get("stock")
        if stock is not None and stock <= 0:
            logger.debug("L2: item %s out of stock, skipping", item_id)
            return None

        body = f"【系統自動標註：此商品售價為 {int(price)} 元】\n{name}"
        # shop_location is the seller's city — better than making the LLM guess
        # a location out of a title that never mentions one.
        if (loc := (item.get("shop_location") or "").strip()):
            body += f"\n賣家所在地：{loc}"

        return RawListing(
            url=f"https://shopee.tw/product/{shop_id}/{item_id}",
            title=name,
            body_content=body[:800],  # L3
            source="shopee",
            status="available",
        )

    # ------------------------------------------------------------------
    # Detail page: multi-variant flattening (L2 + L3 gatekeeper entry)
    # ------------------------------------------------------------------

    async def _fetch_item_details(self, context: BrowserContext, item: dict) -> list[RawListing]:
        shop_id = item.get("shopid")
        item_id = item.get("itemid")
        base_name = item.get("name", "")

        if not shop_id or not item_id:
            return []

        url = f"https://shopee.tw/product/{shop_id}/{item_id}"
        listings: list[RawListing] = []

        async with self._sem:
            page = await context.new_page()
            try:
                item_data: dict = {}

                _DETAIL_PATTERNS = ("api/v4/item/get", "api/v4/pdp/get_pc", "api/v4/pdp/get")

                async def handle_item_response(response):
                    if any(p in response.url for p in _DETAIL_PATTERNS):
                        logger.info("Detail API hit: %s", response.url)
                        try:
                            data = await response.json()
                            # pdp/get_pc → data.item
                            # older item/get → data (direct fields)
                            inner = data.get("data") or data.get("item") or {}
                            if isinstance(inner, dict):
                                payload = inner.get("item") or inner
                                if isinstance(payload, dict):
                                    item_data.update(payload)
                        except Exception as e:
                            logger.warning("Failed to parse detail response: %s", e)

                page.on("response", handle_item_response)
                await page.goto(url, wait_until="load", timeout=25000)
                await asyncio.sleep(2)  # allow api/v4/item/get to fire

                try:
                    await self._human_interact(page)
                    await self._human_delay()
                except Exception:
                    pass  # best-effort; don't abort listing extraction

                page.remove_listener("response", handle_item_response)

                if not item_data:
                    logger.warning("No item data intercepted for %s — API endpoint may have changed", url)
                    return []

                desc = item_data.get("description", "")[:800]
                models = item_data.get("models") or item_data.get("model_list") or []

                if not models:
                    # Parent stock fallback
                    stock_v2 = item_data.get("stock_info_v2", {}).get("summary", {}).get("current_stock")
                    if stock_v2 is not None:
                        stock = stock_v2
                    else:
                        stock = item_data.get("stock")
                        if stock is None or stock == 0:
                            stock = item_data.get("normal_stock")

                    if stock is None:
                        stock = 0

                    if stock > 0:
                        price = item_data.get("price_info", {}).get("current_price")
                        if price is None or price == 0:
                            price = item_data.get("price", 0)
                        price = price / 100000

                        listings.append(RawListing(
                            url=url, title=base_name,
                            body_content=f"【系統自動標註：此商品售價為 {int(price)} 元】\n" + desc, source="shopee", status="available",
                        ))
                else:
                    survivors: list[dict] = []
                    for model in models:
                        # status: 1=normal, 0/2=disabled/sold-out
                        if model.get("status", 1) != 1:
                            continue
                        # has_stock: use bool() to handle int 1/0 from API (not `is True`)
                        if not bool(model.get("has_stock", False)):
                            continue
                        # is_grayout: same, use bool() to avoid int mismatch
                        if bool(model.get("is_grayout", False)):
                            continue
                        # stock_info_v2 numeric guard
                        stock_v2 = (
                            model.get("stock_info_v2", {})
                                .get("summary", {})
                                .get("current_stock")
                        )
                        if stock_v2 is not None and stock_v2 <= 0:
                            continue

                        price_raw = _extract_model_price(model)
                        if price_raw == 0:
                            continue

                        survivors.append(model)

                    # Diversity Guard: keep cheapest 5 variants per item
                    survivors.sort(key=lambda m: _extract_model_price(m))
                    for model in survivors[:5]:
                        price_raw = _extract_model_price(model)
                        price = price_raw / 100000
                        model_id = model.get("modelid") or model.get("model_id")
                        model_name = model.get("name", "")
                        listings.append(RawListing(
                            url=f"{url}?m={model_id}",
                            title=f"{base_name} - {model_name}",
                            body_content=f"【系統自動標註：此規格售價為 {int(price)} 元】\n" + desc, source="shopee", status="available",
                        ))

            except Exception as e:
                logger.warning("Error fetching detail %s: %s", url, e)
            finally:
                try:
                    page.remove_listener("response", handle_item_response)
                except Exception:
                    pass
                await page.close()

        return listings
