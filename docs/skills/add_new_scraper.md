# Skill：新增平台爬蟲

> 適用情境：想新增蝦皮、臉書社團、露天等平台的二手機資料來源。
> 閱讀 spec 前置：`openspec/specs/scraper/spec.md`

---

## 核心原則

本專案採用 **Strategy Pattern**。新平台爬蟲必須繼承 `BaseScraper`，pipeline 不需要知道平台細節。**禁止修改 `src/main.py` 或 `backend/pipeline.py` 的核心邏輯。**

---

## 步驟一：閱讀介面契約

先確認 `src/scrapers/base.py` 的抽象介面，確保你的實作符合契約：

```python
# src/scrapers/base.py（只讀，禁止修改）
class BaseScraper(ABC):
    @abstractmethod
    async def fetch_listings(self) -> list[RawListing]:
        """爬取列表頁，回傳所有候選物件"""
        ...

    @abstractmethod
    async def fetch_detail(self, url: str) -> str:
        """爬取單一物件的完整內文"""
        ...
```

`RawListing` dataclass 欄位：`url`, `title`, `body_content`, `source`, `status`。

---

## 步驟二：建立新爬蟲檔案

在 `src/scrapers/` 下新增一個 Python 檔，以蝦皮為例：

```
src/scrapers/shopee.py
```

**最小實作範本：**

```python
import asyncio
import logging
import os
from typing import Optional

from playwright.async_api import async_playwright, Browser

from src.scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

_SOLD_KEYWORDS = ["售出", "已售出", "Sold", "sold", "已出"]


class ShopeeScraper(BaseScraper):
    def __init__(self):
        self._sem = asyncio.Semaphore(int(os.getenv("SCRAPER_CONCURRENCY", "5")))
        self._delay = float(os.getenv("SCRAPER_DELAY_SECONDS", "1"))

    async def fetch_listings(self) -> list[RawListing]:
        # 1. 取得候選 URL 列表（可用 API、RSS 或 playwright 爬列表頁）
        candidates: list[tuple[str, str]] = []  # (url, title)
        # ... 你的邏輯 ...

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                tasks = [self._fetch_one(browser, url, title) for url, title in candidates]
                results = await asyncio.gather(*tasks)
            finally:
                await browser.close()

        return [r for r in results if r is not None]

    async def fetch_detail(self, url: str) -> str:
        async with self._sem:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    # 調整 selector 為蝦皮的商品描述元素
                    el = await page.query_selector(".product-description")
                    return await el.inner_text() if el else ""
                except Exception as e:
                    logger.warning("fetch_detail failed for %s: %s", url, e)
                    return ""
                finally:
                    await browser.close()

    async def _fetch_one(self, browser: Browser, url: str, title: str) -> Optional[RawListing]:
        async with self._sem:
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                # 調整 selector
                el = await page.query_selector(".product-description")
                text = await el.inner_text() if el else ""
                await asyncio.sleep(self._delay)
                is_sold = any(kw in text for kw in _SOLD_KEYWORDS)
                return RawListing(
                    url=url,
                    title=title,
                    body_content=text,
                    source="shopee",     # ← 每個平台必須有唯一的 source 字串
                    status="sold" if is_sold else "available",
                )
            except Exception as e:
                logger.warning("Scrape failed for %s: %s", url, e)
                return None
            finally:
                await page.close()
```

**關鍵規則：**
- `source` 欄位必須是唯一的平台識別字串（如 `"shopee"`），不得重複使用 `"ptt"`
- 售出偵測必須搜尋**全文**，使用 `_SOLD_KEYWORDS` 清單
- 必須使用 `async_playwright`，**禁止** `sync_playwright`
- 必須使用 `asyncio.Semaphore` 控制並發數，讀取 `SCRAPER_CONCURRENCY` env var
- 個別頁面失敗時回傳 `None` 並 `logger.warning()`，不中斷整批爬取

---

## 步驟三：在 pipeline 中注入新爬蟲

**pipeline 的爬蟲清單通常在 `src/main.py` 或 `backend/pipeline.py`**，找到爬蟲初始化的地方，新增你的爬蟲實例。以 `src/main.py` 為例：

```python
# 修改前
from src.scrapers.ptt import PTTScraper
scrapers = [PTTScraper()]

# 修改後
from src.scrapers.ptt import PTTScraper
from src.scrapers.shopee import ShopeeScraper
scrapers = [PTTScraper(), ShopeeScraper()]
```

pipeline 會對每個 scraper 呼叫 `fetch_listings()`，對回傳的 `RawListing` 清單統一進行 parse → score → upsert，**無需其他修改**。

---

## 步驟四：驗證

1. 單獨測試新爬蟲：
   ```python
   import asyncio
   from src.scrapers.shopee import ShopeeScraper
   listings = asyncio.run(ShopeeScraper().fetch_listings())
   print(f"Got {len(listings)} listings")
   ```

2. 確認 `source` 欄位正確填入資料庫後，API filter `?source=shopee` 可正確篩選。

---

## 常見錯誤

| 錯誤 | 正確做法 |
|------|---------|
| 修改 `pipeline.py` 加 `if source == "shopee"` 分支 | 用 Strategy Pattern，pipeline 不感知平台 |
| `source="ptt"` 複製貼上忘記改 | 每個平台必須有唯一 source 字串 |
| 只搜尋 body 前 100 字偵測售出 | 必須搜尋 `text`（完整內文） |
| `sync_playwright` + `ThreadPoolExecutor` | 必須用 `async_playwright` |
