### 5.X LLM Parser

**目標**：把爬蟲取得的標題與內文，轉成 `MacBookSpec` 所需的結構化欄位。

---

#### 5.X.1 解析順序

`parse_deal_llm()` 採「規則優先、LLM 補漏」：

1. **PTT 結構化區塊**（`text_extractor.py`）：`[售價]`、`[交易方式/地點]`、`[保固]`、`[規格]`
2. **Regex 抽取**：RAM／SSD／螢幕尺寸，來源優先序為 **標題 > `[規格]` 區塊 > 全文**
3. **LLM**（Gemini）：填補前兩者拿不到的欄位

規則層先跑不是為了省 LLM 額度，而是**規則的結果可預測、可測試**；LLM 只負責處理規則涵蓋不到的自由文字。

#### 5.X.1a 桌機（Mac mini／Mac Studio）

- **`series` 由標題決定，不信 LLM 也不信舊回退。** `detect_product(title)` 判為桌機者，
  `series` 直接寫成 `Mac mini`／`Mac Studio`。舊的回退 `"Air" if "air" in title else "Pro 13"`
  會把每一台 Mac mini 存成 13 吋 Pro；它現在只在標題不是桌機、且 LLM 沒給合法值時才生效。
- **桌機的 `screen_size` 與 `battery_health` 在清理階段強制設為 `None`**，不靠 prompt 自律：
  螢幕尺寸的 regex 會掃整篇內文，賣家提到自己外接的 27 吋螢幕就會被當成機器尺寸。
- 年份推論多一張桌機表（`_DESKTOP_YEARS`，以完整晶片名為 key）：mini M1→2020、
  M2／M2 Pro→2023、M4／M4 Pro→2024；Studio M1 Max／Ultra→2022、M2 Max／Ultra→2023、
  M4 Max／M3 Ultra→2025。表裡沒有的晶片維持賣家寫的年份。
- prompt 的 `chip` 改為描述性規則（世代 + 可選的 Pro／Max／Ultra），不再列舉到 M4 為止——
  與 score-engine spec「不得寫死世代清單」同一條規則。
- **解析迴圈的 `needs_fix` 不對桌機要求 `screen_size`**（`src/main.py`）。否則每一筆桌機
  永遠是「不完整」：解析指紋擋得住重複的 LLM 呼叫，但那是靠第二道防線硬撐。

#### 5.X.2 RAM／SSD 抽取規則（`extract_specs_from_text`）

- **只接受 Apple 實際出貨的規格**：
  - RAM `{8, 16, 18, 24, 32, 36, 48, 64, 96, 128}`
  - SSD `{128, 256, 512, 1024, 2048, 4096, 8192}`（GB）
- **必須先移除核心數樣式** `\d+C\d+G`（如 `8C10G`、`10C/10G`）。
  這是 CPU／GPU 核心數，不是記憶體。實際發生過的錯誤：
  `『澄橘』Macbook Pro 13 2022 M2 8C10G/8G/256G` 被解讀為 **RAM=10、SSD=8192（8TB）**，
  而 SSD ≥1TB 會取得 VFM 加成，導致分數被高估。
- **配對樣式找不到合理值時必須繼續往後找，不得放棄**。
  `8C7G/8G/256G` 舊版撞到 `7G/8G`（RAM=7 不合法）就回傳 `None`，
  沒有繼續找到後面正確的 `8G/256G`。
- 支援的分隔：`/`、`+`、空白。單位 `T`／`TB` 及裸數字 `1/2/4/8` 一律換算為 GB。
- `G` 後面可直接接中文（`16G記憶體`），樣式不得在單位後強制 `\b`。
- **寧可回傳 `None` 也不要猜**。缺值由 LLM 補、或由計分器套用預設值；
  錯值會直接進入 VFM 公式，比缺值傷害更大。

#### 5.X.3 資料修復

`src/scripts/repair_specs.py` 掃描全庫並修正不可能的規格值：

- 預設為試算，`--apply` 才寫入。
- **RAM 與 SSD 是同一次解析的產物，任一不合法即視為整組不可信**——
  倖存的那一半不會因為「剛好是合法規格」就被保留。
  例：`RAM=10 / SSD=8192`，8TB 本身合法，但該機型（13" M2 Pro）不可能，
  且標題明確寫著 256G。
- 標題無法判定時清為 `None`，不做猜測。

#### 5.X.4 精簡模式的影響

蝦皮精簡模式（`SHOPEE_SKIP_DETAILS=true`）沒有商品描述，`body_content` 僅含標題與價格標註。
實測 35 筆新物件的欄位覆蓋率變化：

| 欄位 | 詳情頁模式 | 精簡模式 |
|---|---|---|
| ram_gb | 79.3% | 37.1% → 修正 regex 後可回升 |
| ssd_gb | 80.5% | 40.0% → 同上 |
| location | 6.9% | 71.4%（改用 `shop_location`） |
| chip / release_year / price | ~80% | ~74% |

**約半數蝦皮標題本身就沒寫 RAM／SSD**（例：`Apple MacBook Air 13吋 M1 2020 超值二手蘋果筆電`）。
這是精簡模式無法規避的先天限制，不是 regex 可以解決的問題。
