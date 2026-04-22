# mac-valuer — 最高指導原則

> 本文件是本專案的架構聖經。每一次重構、每一個新功能，都必須符合這份規格的精神。
> 最後更新：2026-04-23（v1.1，同步實際實作狀況）

---

## 專案目標

爬取 PTT MacShop 的二手 MacBook 販售文章，透過 LLM 解析規格、計算 VFM（Value for Money）分數，提供公開 Dashboard 讓使用者找到最划算的二手機器。

---

## 目標架構（重構後）

```
mac-valuer/
├── backend/
│   ├── scrapers/
│   │   ├── base.py           ← 抽象介面 (Strategy Interface / ABC)
│   │   └── ptt.py            ← PTT 實作 (async_playwright + asyncio)
│   ├── parser/
│   │   ├── llm_parser.py     ← Gemini 解析，定義嚴格 JSON schema
│   │   └── text_extractor.py ← Regex fallback，獨立模組
│   ├── models/
│   │   └── mac_spec.py       ← Pydantic model，含新增 Optional 欄位
│   ├── database/
│   │   └── db_manager.py     ← SQLAlchemy ORM，正確 upsert
│   ├── calculator/
│   │   └── score_engine.py   ← 動態權重，VFM 含 SSD 因子
│   └── pipeline.py           ← 主流程 (async)
├── api/
│   └── main.py               ← FastAPI，接收前端過濾參數
├── frontend/
│   └── dashboard.py          ← Streamlit（消費 API，不直連 DB）
├── cronjob.py                ← 排程入口
├── .env.example              ← 環境變數範本
└── requirements.txt
```

---

## 資料庫策略

### 原則：SQLite → PostgreSQL 無痛切換

- **P0–P2（本地開發）**：SQLite + SQLAlchemy
- **P3–P4（上雲端）**：只需修改 `.env` 中的 `DATABASE_URL`，不改任何程式碼

### 連線字串管理

所有資料庫連線必須透過環境變數注入，**禁止任何 hardcode 路徑**：

```env
# .env（本地 SQLite）
DATABASE_URL=sqlite:///./mac_deals.db

# .env（雲端 PostgreSQL）
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/mac_valuer
```

DBManager 透過 SQLAlchemy `create_engine(os.getenv("DATABASE_URL"))` 取得連線。

---

## 資料庫 Schema

```sql
CREATE TABLE deals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,  -- PostgreSQL 改 SERIAL
    url          TEXT UNIQUE NOT NULL,
    source       TEXT NOT NULL DEFAULT 'ptt',        -- 為蝦皮等來源預留
    title        TEXT,
    body_content TEXT,
    parsed_json  TEXT,                               -- SQLite 用 TEXT；PostgreSQL 改 JSONB
    status       TEXT DEFAULT 'available',           -- 'available' | 'sold'
    first_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 永不覆寫
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- 每次更新時刷新
);
```

**重要規則：**
- `first_seen` 在 INSERT 時設定，之後的 upsert **絕對不覆寫**
- `updated_at` 每次 upsert 都刷新
- 爬到「售出」文章時：更新 `status = 'sold'`，**不刪除資料列**
- 使用 SQLAlchemy `on_conflict_do_update` / `insert().prefix_with("OR IGNORE")` 取代 `INSERT OR REPLACE`

---

## 爬蟲層：Strategy Pattern

### 核心原則

**新增蝦皮（或任何平台）爬蟲時，禁止修改核心 pipeline 邏輯。**

### BaseScraper 介面規範

```python
# backend/scrapers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class RawListing:
    url: str
    title: str
    body_content: str
    source: str  # 'ptt' | 'shopee' | ...

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

### PTT 爬蟲規範

- 使用 `async_playwright`，**禁止** `sync_playwright` + `ThreadPoolExecutor`
- 使用 `asyncio.gather` 並行抓取內文（設定合理 semaphore，建議 `asyncio.Semaphore(5)`）
- 「售出」偵測：搜尋關鍵字清單 `["售出", "已售出", "Sold", "sold", "已出"]`，搜尋範圍為**全文**，不限前 100 字

---

## MacBookSpec 模型規範

```python
class MacBookSpec(BaseModel):
    # 核心欄位（必填，無預設）
    chip: Optional[str] = None          # 找不到時存 None，禁止預設為 "M1"
    ram_gb: Optional[int] = None
    ssd_gb: Optional[int] = None
    screen_size: Optional[float] = None
    release_year: Optional[int] = None
    series: Optional[ModelSeries] = None
    price: Optional[float] = None
    location: Optional[str] = None

    # 新增 Optional 欄位（LLM 無法解析時存 NULL，禁止猜測預設值）
    battery_health: Optional[int] = None    # 電池健康度，百分比整數，e.g., 89
    warranty_status: Optional[str] = None   # 保固狀態，e.g., "2025-12", "已過保"
    condition: Optional[str] = None         # 外觀成色，e.g., "全新", "輕微使用痕跡"

    # 中繼資料
    is_year_inferred: bool = False
    is_spec_inferred: bool = False
```

**關鍵規則：`chip` 找不到時回傳 `None`，不得 fallback 為任何值。下游遇 `None` 跳過評分。**

---

## LLM Parser 規範

### Gemini 模型

- 正確 model ID：`gemini-2.0-flash-lite`（或透過 env var `GEMINI_MODEL` 注入）
- **禁止** hardcode 不存在的 model ID

### Prompt 規範

Prompt 中必須明確定義回傳的 JSON schema，範例：

```
請回傳以下 JSON 格式（無法確認的欄位請填 null，禁止猜測）：
{
  "chip": "M2 Pro" | null,
  "ram_gb": 16 | null,
  "ssd_gb": 512 | null,
  "screen_size": 14.0 | null,
  "release_year": 2023 | null,
  "series": "Air" | "Pro 13" | "Pro 14/16" | null,
  "price": 35000 | null,
  "location": "台北/新竹" | null,
  "battery_health": 89 | null,
  "warranty_status": "2025-12" | null,
  "condition": "輕微使用痕跡" | null,
  "status": "available" | "sold"
}
```

---

## Score Engine 規範

### 動態權重

VFM 公式必須接受 `ScoringWeights` 參數物件，支援前端傳入自訂權重。

**機型×螢幕組合加成（取代舊版分離的機型加成 + 螢幕加成）：**

| 組合 | 預設加權 | 說明 |
|------|---------|------|
| Air 13" | 1.00 | 基準 |
| Air 15" | 1.08 | 較大螢幕 |
| Pro 13" | 1.00 | 和 Air 13" 同等，差異悠晶片基準分體現 |
| Pro 14" | 1.18 | ProMotion + Mini-LED + 齐全 port |
| Pro 16" | 1.22 | 旗艦散熱 + 最大螢幕 |

```python
# 實際實作：dashboard.py 的 _recalc_vfm
class ScoringWeights(BaseModel):
    ram_multiplier: float = 1.25   # RAM ≥16GB 時套用
    ssd_multiplier: float = 1.10   # SSD ≥1TB 時套用
    # 機型×螢幕組合加權（取代舊版分離的 model_weight + screen_weight）
    form_air13:  float = 1.00
    form_air15:  float = 1.08
    form_pro13:  float = 1.00
    form_pro14:  float = 1.18
    form_pro16:  float = 1.22

def get_vfm_score(spec: MacBookSpec, weights: ScoringWeights = ScoringWeights()) -> float:
    ...
```

### VFM 公式

```
VFM = (晶片基準分 × 0.9^age × RAM加成 × SSD加成 × 形態加成) ÷ 售價 × 1000

其中：
- 晶片基準分：M1=8500 / M2=10000 / M3=11500 / M4=14500（Pro/Max 更高）
- age = 今年 - release_year
- RAM加成：≥1 TB 時套用 ram_multiplier
- SSD加成：≥1 TB 時套用 ssd_multiplier
- 形態加成：上方表格中對應的 form_* 權重
```

### VFM 分數顏色分級（Dashboard 展示）

顏色閾値依據**全庫所有資料（不受篩選影響）**的百分位數計算，常饁4鐵包官方統計：

| 顏色 | 閾値 | 含義 |
|------|------|------|
| 🟢 綠 | ≥ p75 | 全庫前 25%，優谀 |
| 🟡 黃 | ≥ p50 | 全庫前 50%，普通 |
| 🔴 紅 | < p50 | 全庫後 50%，偏貴 |

### MacBook Pro M2 年份修正

`infer_correct_year` 中，MacBook Pro M2（`M2 Pro` / `M2 Max`）的 `release_year` 應為 **2023**，不是 2022。

---

## API 層規範（FastAPI）

前端 Dashboard **禁止直連資料庫**，必須透過 API 查詢。

```
GET  /api/deals
     ?min_price=20000&max_price=30000
     &ram_gb=16
     &chip=M3
     &status=available          ← 預設只回傳 available
     &source=ptt

POST /api/score/calculate
     body: { spec: MacBookSpec, weights: ScoringWeights }
     → 回傳 vfm_score: float
```

---

## 階段任務（Phase Plan）

| Phase | 範圍 | 狀態 |
|-------|------|------|
| **P0** | 修 Gemini model ID；chip fallback 改 None；補 `feedparser` 到 requirements.txt；修 M2 Pro 年份 | ✅ 完成 |
| **P1** | DB 遷移至 SQLAlchemy + env var 連線字串；新增 status/first_seen 欄位；修復 upsert 邏輯 | ✅ 完成 |
| **P2** | 爬蟲全改 async_playwright；建立 BaseScraper 介面；PTT 實作繼承介面 | ✅ 完成 |
| **P3** | Score Engine 動態化（ScoringWeights）；FastAPI 建立 | ✅ 完成 |
| **P4** | MacBookSpec 新增 3 個 Optional 欄位；LLM Prompt 更新 schema；前端消費 API | ✅ 完成 |
| **P5** | Dashboard 形態加成（Air 13/15, Pro 13/14/16 組合）；全庫固定 VFM 顏色閾值；SSD 篩選；緊湊分頁器 | ✅ 完成 |


---

## 已知問題修復清單

| # | 問題 | 修復方式 | Phase |
|---|------|---------|-------|
| 1 | Gemini model ID `gemini-3.1-flash-lite` 不存在 | 改 `gemini-2.0-flash-lite`，env var 注入；目前也可用 `gemini-3.1-flash-lite-preview` | P0 |
| 2 | `sync_playwright` + `ThreadPoolExecutor` 不安全 | 全改 `async_playwright` + `asyncio.gather` | P2 |
| 3 | `clean_data()` import 後從未呼叫（死碼） | 整合進 pipeline | P1 |
| 4 | Chip 找不到時 fallback 預設 `"M1"` | 改為回傳 `None`，下游跳過 | P0 |
| 5 | 售出偵測只看前 100 字 | 全文搜尋多關鍵字，更新 status 欄位 | P2 |
| 6 | VFM 公式忽略 SSD 容量 | 加入 `ssd_multiplier` | P3 |
| 7 | `INSERT OR REPLACE` 蓋掉 `first_seen` 時間戳 | SQLAlchemy upsert，保留 `first_seen` | P1 |
| 8 | MacBook Pro M2 年份誤標為 2022 | 修正為 2023 | P0 |
| 9 | `requirements.txt` 漏 `feedparser` | 補上；同時加入 `sqlalchemy`, `fastapi`, `uvicorn` | P0 |
| 10 | LLM Prompt 無 schema 定義 | Prompt 明確列出所有欄位與型別 | P4 |
| 11 | `except: continue` 吞掉所有錯誤 | 改為 `except Exception as e: logger.warning(...)` | P1 |
| 12 | DB 路徑 hardcode 為相對路徑 | 改由 env var `DATABASE_URL` 控制 | P1 |
