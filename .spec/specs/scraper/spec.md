### 3.W PTT Scraper (MacShop 板)
**目標**：抓取 PTT MacShop 板的二手 Mac 讓售文。

- **實作與繼承**：`PTTScraper` 繼承 `BaseScraper`，`source="ptt"`。
- **傳輸方式**：**純 HTTP，不使用瀏覽器**。清單來自 Atom feed
  （`https://www.ptt.cc/atom/MacShop.xml`），內文來自文章頁的
  `<div id="main-content">`，兩者皆為伺服器端渲染的靜態內容。
  因此**可在 GitHub Actions 上執行**，不依賴本機。

  此處曾以 Playwright 開 Chromium 讀每一篇文章。2026-08-26 的 `ba2d143`
  以「camoufox has no browser installed」為由刪掉 workflow 的
  `playwright install` —— 該理由屬於當時被移出 CI 的蝦皮，未對留在 CI 的 PTT
  檢查，導致 2026-08-26 起連續三晚的排程都在啟動瀏覽器時失敗。

  改為純 HTTP 前已比對過：`_main_content_text()` 與 Playwright 的
  `inner_text()` 在六篇實際文章上，正規化空白後**完全相同**。瀏覽器沒有換到
  任何東西。**不存在的相依不會被忘記**。

- **內文抽取（`_main_content_text`）**：
  - 自 `id="main-content"` 起取到文件結尾，**不嘗試配對巢狀 `</div>`**——
    呼叫端會在簽名分隔線 `--` 處截斷，遠早於頁尾，收尾邊界因此無關緊要。
  - 必須先移除 `<script>` / `<style>`：script 字串中出現的 `</div>`
    會使任何以標籤配對為基礎的作法提前結束。
  - 區塊結束標籤轉為換行、行內標籤移除，此即瀏覽器 `inner_text()` 的規則。
  - 必須 `html.unescape()`：售價常寫成 `&lt;&lt; 32000 &gt;&gt;`，
    不還原則價格 regex 抽不到。
  - 回應必須指定 `encoding = "utf-8"`：PTT 不一定在標頭宣告，
    交給 requests 猜會使全部中文變成亂碼。

- **過濾機制**：標題須含機型名稱與晶片代號，且不含 `_EXCLUDE_TITLES`
  （徵求、交換、Intel 世代）。
- **售出判定**：以 `_SOLD_KEYWORDS` 比對內文。
- **失敗語意**：**feed 取不到任何 entry 時必須拋出例外。**
  feedparser 對網路失敗的回報方式是「一個沒有 entries 的物件」而非例外，
  若照樣回傳空 list，heartbeat 會顯示「0 筆」——與真正的平靜夜晚無法區分，
  正是本專案在別處已修掉的那種混淆。

---

### 3.X Shopee Scraper (蝦皮)
**目標**：抓取蝦皮上的二手 MacBook 拍賣資訊。

- **實作與繼承**：`ShopeeScraper` 繼承自 `BaseScraper`。
- **雙路徑取得策略（Transport Dispatch）**：
  `ShopeeScraper.fetch_listings()` 依環境變數自動選擇資料來源，**切換邏輯必須封裝在 scraper 內**，`src/main.py` 不得出現平台判斷分支。

  | 條件 | 實作 | 檔案 |
  |---|---|---|
  | `SHOPEE_APP_ID` 且 `SHOPEE_APP_SECRET` 皆非空 | 聯盟行銷 Open API（GraphQL `productOfferV2`，SHA256 簽章） | `src/scrapers/shopee_api.py` |
  | 上述任一為空 | camoufox 瀏覽器爬蟲（既有實作） | `src/scrapers/shopee.py` |

  - **API 路徑限制**：`productOfferV2` 不提供商品描述，`body_content` 只能由標題與價格標註組成；亦不提供 model 層級資料，故 Variant Flattening 不適用，改以 `priceMin` 作為入手價、`priceMax` 併入 body 的價格區間註記。
  - **瀏覽器路徑限制**：**不可在 GitHub Actions 等機房環境執行。**

    此限制經 `.github/workflows/shopee-ci-test.yml` 實測確認，非推論。在補齊 session
    還原與 `camoufox fetch` 之後，runner 帶著 22 個有效 cookie 存取搜尋頁，仍被導向：

    ```
    https://shopee.tw/verify/captcha?...&scene=crawler_item&...
    ```

    `scene=crawler_item` 是蝦皮反爬系統對請求的分類標記。session 有效卻仍被攔，
    表示攔截依據是**執行環境（IP 段）**而非憑證。瀏覽器路徑必須在住宅或行動網路
    下執行（`scripts/run_local_scrape.ps1`）。

    需要重新驗證時（例如蝦皮政策改變）可手動觸發該 workflow，它不寫入資料庫。
- **搜尋策略**：
  - 關鍵字：`二手 MacBook` (不依賴 facet id)；可經 `SHOPEE_KEYWORDS` 以逗號擴充。
  - 存取控制：0 登入、隨機 User-Agent、隨機 Delay。
- **失敗語意（Failure Semantics）**：
  - 爬取失敗**必須拋出例外**，不得回傳空 list。空 list 代表「本次無符合物件」，與「爬蟲壞掉」是不同事件，混淆會使 heartbeat 無法反映故障。
  - 瀏覽器路徑在 headless 下撞到登入牆時，拋出 `ShopeeSessionExpired`。
  - Pipeline 收到例外時，該來源必須被記入 `source_errors`，並**跳過該來源的 sweep**（否則會把整批既有物件誤標為 `unavailable`）。
- **下架判定（Retirement）**：
  - 本專案所有爬蟲取得的都是**取樣視窗**，不是完整庫存：PTT 讀 Atom feed 的近期文章，蝦皮讀最新約 180 筆搜尋結果。
  - 因此**不得**以「本次執行沒看到」作為下架依據。實測結果：蝦皮兩次執行的商品集合可能完全不相交（35 個舊商品 vs 30 個新商品，重疊 0），舊集合並非售出，只是被擠出最新清單。
  - 正確作法為 `DBManager.sweep_stale(source, max_age_days)`：僅當 `last_seen` 超過 `STALE_DAYS`（預設 14 天）未更新才標記 `unavailable`。
  - 真正的售出／下架偵測仍由爬蟲自身負責——PTT 靠 `_SOLD_KEYWORDS` 比對內文，蝦皮靠 L2 的 `has_stock` / `is_grayout` / `stock_info_v2` 檢查商品本身。
- **請求量與精簡模式（`SHOPEE_SKIP_DETAILS`，預設 `true`）**：
  - 逐一開啟商品詳情頁的作法，每次執行約產生 **33 次頁面載入**（3 搜尋頁 + 約 30 詳情頁），
    集中在同一 session 的數分鐘內。這是被反爬系統判定為爬蟲的主要特徵。
  - 精簡模式**只讀搜尋頁**，由 `_listing_from_search_item()` 直接組出 `RawListing`：
    搜尋回應本身已含 `name` 與 `price`（L1 過濾器即依賴此二欄位），請求數降為 **3**。
  - 精簡模式下**不適用 Variant Flattening**（無 model 層級資料），一個商品產生一筆；
    亦無商品描述，`body_content` 由標題、價格標註與 `shop_location` 組成。
  - L2 在此模式改以搜尋回應的 `stock` 欄位判定；該欄位缺失時視為未知，予以保留。
  - 設為 `false` 可還原詳情頁模式（資料較完整，但觸發驗證碼的機率顯著較高）。
- **規格處理 (Variant Flattening)**：僅適用於詳情頁模式。
  - 必須將包含多個 Models 的單一 Item 打平。
  - 每個 Model 需轉換為獨立的 `RawListing`。
  - **唯一識別 (URL)**：使用 Synthetic URL 格式 `[原始商品URL]?m=[modelid]`。
- **過濾機制 (Gatekeepers)**：所有 `RawListing` 進入後續流程前需通過：
  1. **L1**: `5000 <= 價格 <= 150000` 且 標題不包含 `['殼', '膜', '零件機', '報廢']`。
  2. **L2**: `stock > 0` (0 視為售出)。
  3. **L3**: `body_content` 最大長度限制為 800 字元。
- **防爆機制 (Safety)**：
  - 支援環境變數 `MAX_LLM_CALLS_PER_RUN` (Default: 30)。
  - 達上限時觸發 Graceful Shutdown。

---

### 3.Y Carousell Scraper (旋轉拍賣)
**目標**：抓取旋轉拍賣上的二手 MacBook。

- **實作與繼承**：`CarousellScraper` 繼承 `BaseScraper`，`source="carousell"`。
- **傳輸方式**：**純 HTTP，不使用瀏覽器**。分類頁與商品頁皆為伺服器端渲染，
  所需欄位全部位於 schema.org 的 `application/ld+json` 區塊。
- **執行位置：本機，不在 GitHub Actions。**
  此處原本寫「因此可在 GitHub Actions 上執行，與 PTT 同級穩定」——推論錯誤。
  2026-08-28 實測：同一個 sitemap 請求，GitHub runner 得到 403，住宅 IP 得到 200
  （五種標頭 × 五個路徑，僅 `robots.txt` 通過）。旋轉拍賣過濾的是**請求來源**，
  不是頁面如何渲染。**不需要瀏覽器，不等於不會被擋。**
  留在 CI 只會每晚發一則假失敗，而每次都響的警報等同於沒有警報。

- **robots.txt 遵循（2026-08-27 查核）**：

  | 規則 | 本實作 |
  |---|---|
  | `Disallow: /search/` | 不使用 |
  | `Disallow: /*?` | 不使用任何帶查詢字串的網址；因此**不能用 `?page=` 分頁** |
  | `Allow: /api-service/*?` | 未使用（端點存在但回應格式未知） |
  | `Sitemap:` | **主要入口** |

  站方明文提供 sitemap 並禁止搜尋頁，實作必須照此區分。此為與蝦皮的根本差異：
  蝦皮無此類規範且主動封鎖，旋轉拍賣則明示哪些可爬。

- **取得流程**：
  1. 讀取商品 sitemap（`CAROUSELL_SITEMAP`，預設 computers-tech，約 9,400 筆商品、2.5MB）
  2. 以解碼後的網址 slug 比對機型與排除字詞，依 `<lastmod>` 排序取最新 `CAROUSELL_MAX_ITEMS` 筆
  3. 逐一取商品頁，解析 JSON-LD `@type=Product`

- **過濾機制**：
  1. **L1**：`5000 <= price <= 150000`，且標題與 slug 均不含排除字詞。
  2. **標題必須含機型名稱**——僅比對 slug 不足：實測有 Honor 筆電以
     「鋁合金類macbook」進入，另有賣家標題為「888」搭配 99999 佔位價。
  3. **L2**：`offers.availability` 非 `InStock` 即視為售出；另以 `_SOLD_KEYWORDS` 比對描述作為後備。
  4. **L3**：`body_content` 上限 800 字元。

- **失敗語意**：sitemap 取不到任何機型網址時**拋出例外**（sitemap 恆有內容，
  空結果代表格式變更），個別商品頁失敗則記錄 warning 並跳過，不中斷整批。

- **請求量**：1（sitemap）+ `CAROUSELL_MAX_ITEMS`（預設 30），並以 `CAROUSELL_DELAY` 間隔。
