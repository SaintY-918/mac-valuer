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
    下執行（`scripts/run_local_shopee.ps1`）。

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
- **規格處理 (Variant Flattening)**：
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
