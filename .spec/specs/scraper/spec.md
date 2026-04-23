### 3.X Shopee Scraper (蝦皮)
**目標**：抓取蝦皮上的二手 MacBook 拍賣資訊。

- **實作與繼承**：`ShopeeScraper` 繼承自 `BaseScraper`。
- **搜尋策略**：
  - 關鍵字：`二手 MacBook` (不依賴 facet id)。
  - 存取控制：0 登入、隨機 User-Agent、隨機 Delay。
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
