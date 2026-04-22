# PTT 爬蟲邏輯說明

> 本文件解釋整個資料蒐集、解析與修補流程，以及儀表板欄位缺失的根本原因。

---

## 整體流程（四個步驟）

```
[Step 1] RSS 抓取 + 標題過濾
       ↓
[Step 2] Playwright 抓取內文 + 售出偵測
       ↓
[Step 3] LLM 解析 (Gemini) + Regex 備援
       ↓
[Step 4] 修補迴圈 (Repair Loop) → 存入 DB
```

---

## Step 1：RSS Feed 的天然限制

**來源：** `https://www.ptt.cc/atom/MacShop.xml`

PTT 的 Atom Feed **只包含最新的約 20 篇文章**。這是 PTT 官方 RSS 的硬限制，無法透過參數調整。

每次執行 pipeline，最多只會看到當下版面最新的 20 篇文，不是歷史全部文章。

### 標題過濾（在 RSS 這一層就剔除）

只有同時滿足以下條件的文章才會進入爬取隊列：

| 條件 | 規則 |
|------|------|
| 包含 "macbook"（不分大小寫） | 排除非 MacBook 的販售（如 iPad） |
| 包含 M 晶片關鍵字 | `m1`, `m2`, `m3`, `m4`（不分大小寫） |
| **不**包含排除標籤 | `[徵]`, `[交換]`, `intel`, `i5`, `i7`, `i9`, `2017`, `2018` |

範例：

- ✅ `[販售] MacBook Air M2 16/512` → 通過
- ❌ `[徵] MacBook M3` → 被 `[徵]` 排除
- ❌ `[販售] MacBook Pro 2018 intel i7` → 被 `intel`/`i7`/`2018` 排除
- ❌ `[販售] iPad Air M2` → 不含 "macbook" 排除

---

## Step 2：Playwright 內文爬取

通過過濾的文章 URL 會用 Playwright（headless Chromium）並行抓取內文。

### 並行控制

```python
_sem = asyncio.Semaphore(SCRAPER_CONCURRENCY)  # 預設 5
```

最多 5 個頁面同時開啟，每頁抓完後暫停 `SCRAPER_DELAY_SECONDS`（預設 1 秒），避免對 PTT 造成壓力。

### 內文擷取

```python
el = await page.query_selector("#main-content")
text = el.inner_text()
# 去掉 PTT 推文（"--" 分隔線後的部分）
text = text.split("--")[0]
```

**只保留正文**，推文 (`--` 之後的內容) 不送給 LLM。

### 售出偵測（全文搜尋）

```python
_SOLD_KEYWORDS = ["售出", "已售出", "Sold", "sold", "已出"]
is_sold = any(kw in text for kw in _SOLD_KEYWORDS)
```

搜尋範圍是**整篇正文**（不限前幾個字）。偵測到任一關鍵字，`status` 就設為 `"sold"`。

---

## Step 3：LLM 解析 + Regex 備援

### LLM 解析（Gemini）

`parse_deal_llm(title, body_content)` 將清理後的標題和內文送給 Gemini，要求回傳嚴格 JSON schema：

```
chip, ram_gb, ssd_gb, screen_size, release_year, series,
price, location, battery_health, warranty_status, condition
```

Prompt 明確要求：**無法確認的欄位填 null，禁止猜測。**

### 資料清理優先順序（在 LLM 回傳後套用）

1. **RAM / SSD**：Regex 先從標題和內文提取（slash 格式如 `16/512`），Regex 有結果就**覆蓋** LLM 值。
2. **螢幕尺寸**：LLM 優先。LLM 是 null 時，才用 Regex 從標題/內文找 `13"`, `14吋`, `16-inch` 等。
3. **release_year**：執行 `infer_correct_year()` 根據晶片 + 系列修正（如 M2 Pro → 2023）。
4. **chip**：LLM 回傳 null 時，不填入任何預設值。

### 各欄位的失敗模式

| 欄位 | 常見失敗原因 |
|------|------------|
| `chip` | 標題簡寫（如 "Air M2 16/512"，省略了 MacBook），LLM 無法確認 |
| `ram_gb` / `ssd_gb` | 沒有明確數字，或格式特殊（如 "一T"）Regex 找不到 |
| `price` | 賣家把價格放在圖片、表情符號或非標準格式中 |
| `location` | 只寫縣市縮寫、或完全沒寫地點 |
| `screen_size` | 沒寫吋數（常見於只寫型號的短標題） |
| `battery_health` | 賣家沒提就是 null（正確行為） |

---

## Step 4：修補迴圈（Repair Loop）

`run_valuation_pipeline()` 中的 Step 2 會對資料庫**所有**資料跑修補迴圈。

### 觸發條件（目前的邏輯）

```python
needs_fix = (
    not p_json                      # 從未解析過
    or not p_json.get("chip")       # chip 是 None
    or p_json.get("chip") == "None" # chip 是字串 "None"（舊 bug）
    or p_json.get("location") == "未知"  # 地點未知
)
```

**只有上述四種情況才會重新呼叫 LLM**。

---

## 為什麼儀表板有這麼多缺失欄位？

### 根本原因

修補迴圈的觸發條件**太窄**，只看 `chip` 和 `location`。

如果某筆資料已經有 `chip`（例如 "M2"），即使 `ram_gb`、`ssd_gb`、`price`、`screen_size` 全是 null，修補迴圈也**不會**重新解析它。

### 時間差問題

螢幕尺寸的 Regex 備援功能（`extract_screen_size_from_text`）是在本次重構中新增的。在此之前已存入 DB 的資料都是用舊版 parser 解析的，`screen_size` 會是 null，且不會被修補迴圈觸發。

### 修補迴圈的預設值問題（⚠️ 需注意）

當修補迴圈觸發時，它會用 `setdefault` 填入以下假值：

```python
res_dict.setdefault("release_year", 2020)  # 猜 2020
res_dict.setdefault("series", "Air")       # 猜 Air
res_dict.setdefault("screen_size", 13.3)   # 猜 13.3 吋
```

這表示被修補的資料 `screen_size` 不是真實值，而是 fallback 的 13.3。這與 MacBookSpec 「無法確認時存 null」的原則相衝突。

---

## 建議修復方向

### 1. 拓寬修補迴圈的觸發條件

```python
needs_fix = (
    not p_json
    or not p_json.get("chip")
    or p_json.get("chip") == "None"
    or p_json.get("location") == "未知"
    or not p_json.get("ram_gb")        # ← 新增
    or not p_json.get("ssd_gb")        # ← 新增
    or not p_json.get("price")         # ← 新增
    or not p_json.get("screen_size")   # ← 新增
)
```

### 2. 移除修補迴圈中的假 setdefault

```python
# 移除這三行，讓欄位保持 None 而非猜測值
# res_dict.setdefault("release_year", 2020)
# res_dict.setdefault("series", "Air")
# res_dict.setdefault("screen_size", 13.3)
```

### 3. 手動強制重新解析全部 DB

執行以下命令可以清空所有 `parsed_json`，讓下次 pipeline 強制重新解析全部資料：

```bash
python -c "
from src.database.db_manager import DBManager
from sqlalchemy import text
db = DBManager()
with db.Session() as s:
    s.execute(text('UPDATE deals SET parsed_json = NULL'))
    s.commit()
print('Done — all records cleared for re-parsing')
"
```

重新解析後再執行 pipeline：

```bash
python -m src.main
```

---

## 資料流程圖（完整）

```
PTT RSS Feed (~20 篇)
       │
       ▼
  標題過濾
  (chip/macbook/排除標籤)
       │
       ▼
Playwright 並行抓內文
(semaphore=5, delay=1s)
       │
       ├─── 已售出？→ status="sold"
       │
       ▼
  存入 DB (upsert)
  first_seen 永不覆寫
       │
       ▼
修補迴圈 (for 全部 DB 資料)
  chip/location 缺失 → 重新 LLM
       │
       ▼
  LLM 解析 (Gemini)
  + Regex 備援 (RAM/SSD/screen_size)
       │
       ▼
  parsed_json 更新至 DB
       │
       ▼
  API (/api/deals) → Dashboard
```
