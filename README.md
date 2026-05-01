# mac-valuer

二手 MacBook 行情爬蟲與估價系統。自動從 PTT MacShop 爬取販售文章，透過 LLM 解析規格，計算 VFM（Value for Money）分數，找出市場上最划算的機器。

---

## 系統架構概覽

```
PTT MacShop RSS
      ↓
  爬蟲 (async Playwright)
      ↓
  LLM 解析 (Gemini) + Regex fallback
      ↓
  SQLite 資料庫 (本地) / PostgreSQL (雲端)
      ↓
  VFM 評分引擎 (動態權重)
      ↓
  FastAPI  ←→  Streamlit Dashboard
```

---

## 環境設定

### 1. 安裝 Python 依賴

建議使用 Python 3.11+。

```bash
pip install -r requirements.txt
```

安裝 Playwright 瀏覽器核心（首次需執行）：

```bash
playwright install chromium
```

### 2. 設定環境變數

複製範本並填入你的金鑰：

```bash
cp .env.example .env
```

開啟 `.env`，至少填入以下兩項才能正常運作：

| 變數 | 說明 |
|------|------|
| `GEMINI_API_KEY` | Google AI Studio 取得的 API Key |
| `DATABASE_URL` | 本地開發保持預設即可；上雲端時改成 PostgreSQL 連線字串 |
| `DISCORD_WEBHOOK_URL` | （選用）Discord 頻道 Webhook URL；若未設定則撿漏推播自動停用、pipeline 不報錯 |
| `ALERT_VFM_THRESHOLD` | （選用）VFM 推播閾值，預設 `500`；只在 `vfm_score > threshold` 時觸發 Discord 推播 |

---

## 零成本雲端部署（P7）

```
GitHub Actions (cron 每天 UTC 18:00 = 台灣 02:00)
  └─ python -m src.main
       └─ 寫入 ──► Neon PostgreSQL (免費 0.5 GB, sslmode=require)
                              ▲
              Streamlit Community Cloud (免費, 公開)
              直連 DBManager.get_filtered_deals()
```

### 部署步驟

1. **Neon PostgreSQL**：在 [neon.tech](https://neon.tech) 建立免費 project，取得連線字串（格式：`postgresql+psycopg2://user:pass@host.neon.tech/dbname?sslmode=require`）。首次本地執行 `DATABASE_URL=<neon_url> python -m src.main` 以自動建表。

2. **GitHub Secrets**（`Settings → Secrets → Actions`）：

   | Secret 名稱 | 內容 |
   |---|---|
   | `DATABASE_URL` | Neon 連線字串 |
   | `GEMINI_API_KEY` | Google AI Studio Key |
   | `DISCORD_WEBHOOK_URL` | Discord Webhook URL（選用） |
   | `ALERT_VFM_THRESHOLD` | VFM 推播閾值，預設 500（選用） |

3. **GitHub Actions**：push `.github/workflows/scraper.yml` 後自動啟用。可到 Actions 頁面手動 dispatch 測試。每天執行完畢後 Discord 會收到「每日巡邏完畢」heartbeat 通知。

4. **Streamlit Community Cloud**：連結 GitHub repo → Main file path 設為 `streamlit_app.py` → Secrets 貼入：
   ```toml
   DATABASE_URL = "postgresql+psycopg2://...?sslmode=require"
   ```
   Dashboard sidebar 會顯示「🔄 資料庫最後更新時間」確認資料新鮮度。

---

## 執行指令（本地開發）

### 手動跑一次完整爬蟲 + 估價

```bash
python -m src.main
```

執行完畢後會在根目錄產生 `valuation_report.csv`，並在終端機印出前 15 名高 VFM 機器。

### 啟動 Streamlit Dashboard

```bash
streamlit run src/dashboard.py
```

Dashboard 直連 `DATABASE_URL` 指定的資料庫（本地 SQLite 或 Neon PostgreSQL）。

### 啟動 FastAPI 伺服器（本地開發用）

```bash
uvicorn api.main:app --reload --port 8000
```

API 文件自動產生於：`http://localhost:8000/docs`

### 首次部署前抑制歷史爆推

```bash
python -m src.scripts.suppress_initial_burst
```

將現有資料的 `last_alerted_price` 設為目前 `price`，避免第一次啟用 Discord Webhook 時大量舊資料全部觸發推播。

### 排程自動爬取（本地測試用）

使用系統 cron 或 Windows 工作排程器執行：

```bash
python cronjob.py
```

建議頻率：每天一次（PTT MacShop 發文量約 10–30 篇/天）。

---

## 專案檔案說明

```
src/
├── main.py                ← 主流程入口
├── parser/
│   ├── scraper.py         ← PTT 爬蟲（RSS + Playwright）
│   └── llm_parser.py      ← Gemini LLM 解析 + Regex fallback
├── models/
│   └── mac_spec.py        ← MacBookSpec Pydantic 模型
├── database/
│   └── db_manager.py      ← SQLAlchemy DB 操作
├── calculator/
│   └── score_engine.py    ← VFM 評分公式
├── processor/
│   └── data_filter.py     ← 離群值清洗（IQR）
├── utils/
│   └── benchmark_db.py    ← Apple Silicon 效能基準分數表
└── dashboard.py           ← Streamlit 前端
```

---

## 重構進度

| Phase | 範圍 | 狀態 |
|-------|------|------|
| P0 | Bug 修復（Gemini model ID、chip fallback、年份錯誤） | ✅ 完成 |
| P1 | DB 遷移至 SQLAlchemy + env var + status 欄位 | 待執行 |
| P2 | 爬蟲全改 async + BaseScraper 介面 | 待執行 |
| P3 | Score Engine 動態化 + FastAPI | 待執行 |
| P4 | 新增 Optional 欄位 + 前端消費 API | 待執行 |

詳細架構規格見 [CLAUDE.md](CLAUDE.md)。

---

## 注意事項

- `.env` 已加入 `.gitignore`，請勿將真實 API Key 提交到版本控制。
- 首次執行前請確認 `playwright install chromium` 已完成，否則爬蟲會靜默失敗。
- 本地 SQLite 資料庫預設路徑為專案根目錄的 `mac_deals.db`，可透過 `DATABASE_URL` 修改。
