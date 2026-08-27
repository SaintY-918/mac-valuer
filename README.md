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
  └─ python -m src.main --source ptt   （有 Affiliate 金鑰時為 all）
       └─ 寫入 ─┐
                ├─► Neon PostgreSQL (免費 0.5 GB, sslmode=require)
本機 Windows 排程 ┘                    ▲
  └─ scripts/run_local_shopee.ps1      │
       └─ python -m src.main --source shopee
                                       │
              Streamlit Community Cloud (免費, 公開)
              直連 DBManager.get_filtered_deals()
```

### 為什麼蝦皮要分開跑

蝦皮的**瀏覽器爬蟲無法在 GitHub Actions 上運作**。原本的三個原因裡，前兩個其實都能在 CI 修好：

| # | 原因 | 是否可解 |
|---|---|---|
| 1 | 登入 session 存在本機，沒被帶到 runner 上 | ✅ 可解（存成 secret，見 `export_shopee_session.py`） |
| 2 | `camoufox` 的 Firefox 核心沒安裝（workflow 只裝了 Chromium） | ✅ 可解（`python -m camoufox fetch`） |
| 3 | **蝦皮把 runner 的 IP 段判定為爬蟲** | ❌ **無解** |

第 3 點經實測確認（`.github/workflows/shopee-ci-test.yml`）。修掉前兩點之後，runner 帶著
22 個有效 cookie 存取搜尋頁，仍被導向：

```
https://shopee.tw/verify/captcha?...&scene=crawler_item&...
```

`scene=crawler_item` 是蝦皮反爬系統的分類標記。**session 有效卻照樣被攔，代表攔截看的是
執行環境而不是憑證**——換一台免費的機房主機只是換一個會被擋的 IP。瀏覽器爬蟲必須跑在
住宅或行動網路下。

因此有兩條路徑，由 `ShopeeScraper.fetch_listings()` 自動選擇：

| 條件 | 走的路徑 | 可否在 CI 跑 |
|---|---|---|
| `.env` 有 `SHOPEE_APP_ID` + `SHOPEE_APP_SECRET` | 蝦皮聯盟行銷 Open API（簽章 HTTP，無反爬） | ✅ |
| 兩者留空 | camoufox 瀏覽器爬蟲 | ❌ 僅本機 |

`src/main.py` 不含任何平台判斷，切換完全封裝在 scraper 內（Strategy Pattern，見 `.spec/specs/scraper/spec.md`）。

### 申請蝦皮聯盟行銷 Open API

免費。分潤計畫不收申請費也不收月費——方向相反，有人透過你的連結下單，蝦皮付你佣金（商城約 2~5%，累積滿 NT$500 可提領）。

**先確認申請的是哪一個 API。** 兩者名字很像但完全不同：

| | 蝦皮 Open Platform（賣家 API） | **蝦皮聯盟行銷 Open API** |
|---|---|---|
| 網域 | `partner.shopeemobile.com` | `open-api.affiliate.shopee.tw` |
| 對象 | 商城賣家、ERP 系統供應商 | 分潤計畫推廣夥伴 |
| 本專案要的 | ❌ 不符資格，申請不會過 | ✅ 這個 |

> ⚠️ **Open API 金鑰不能自助申請，須另外聯繫蝦皮開通。**
> 分潤計畫審核通過**不等於**有 API 權限。通過後 `https://affiliate.shopee.tw/open_api`
> 會顯示「您目前無權限申請蝦皮分潤計畫之Open API金鑰。請聯繫我們以開通權限」，
> 且「申請API金鑰」按鈕是停用狀態——必須透過該頁的「聯繫我們」提出申請。
> 在金鑰到手之前，**本機排程（`scripts/run_local_shopee.ps1`）是實際運作中的資料來源，
> 不是過渡方案**。

**申請流程**

1. 到 [affiliate.shopee.tw](https://affiliate.shopee.tw/) 點「開始使用」，用既有蝦皮帳號登入
2. 填個人資料（個人／企業、姓名、聯絡方式）
3. 填**媒體資料**——需提供社群帳號或網站
4. 送出後人工審核，約 **2~5 個工作天**
5. 通過後前往 `https://affiliate.shopee.tw/open_api`（**選單裡沒有這一項，要直接打網址**）
6. 該頁的「聯繫我們」提出開通 Open API 權限的申請，說明用途；開通後才能按「申請API金鑰」
   取得 App ID 與 API 金鑰

**門檻**：任一社群平台至少 300 位好友／追蹤者，或網站具一定流量。被拒可補件重送。

**Product Feed 也一樣被鎖住**：後台「特殊操作 → Product Feed」實測顯示「尚無數據」，
feed 由蝦皮配置給合作夥伴，推廣夥伴無法自行建立。兩條官方路徑都得先請蝦皮開通，
建議在同一封訊息裡一併提出。

**API 端點**：`https://open-api.affiliate.shopee.tw/graphql`（GraphQL，SHA256 簽章）
**互動測試工具**：[Open API Explorer V2](https://open-api.affiliate.shopee.vn/explorer/v2)

**拿到金鑰後，先驗證覆蓋率再切換**

聯盟 API 只收錄**加入分潤計畫的賣場**，二手個人賣家是否涵蓋其中必須實測：

```bash
python -m src.scripts.probe_shopee_affiliate
```

腳本會印出原始筆數、通過 L1 的筆數、標題含二手關鍵字的筆數，並直接給出「值得切換」或「覆蓋率不足，維持瀏覽器爬蟲」的判斷。覆蓋率不足就別填金鑰，繼續走本機排程。

**填入設定**

```bash
# .env（本機）
SHOPEE_APP_ID=你的AppID
SHOPEE_APP_SECRET=你的Secret
```

雲端則加到 GitHub Secrets（見下方部署步驟表格）。只要這兩個值非空，`ShopeeScraper` 就會自動改走 API，不需要改任何程式碼。

**兩個維運注意事項**

- 分潤帳號**可能因長期零轉換被停用**，那會讓 API 這條路突然中斷。Discord heartbeat 會直接報 ⛔ 與原因，不會靜默變成 0 筆。
- `productOfferV2` 除了 `productLink` 還回傳 `offerLink`（你的分潤追蹤連結）。目前實作用 `productLink` 當資料庫主鍵，因為它穩定；`offerLink` 可能帶浮動追蹤參數，直接當主鍵會導致每次執行都新增重複資料。要導購分潤需另加欄位分開存。

### 部署步驟

1. **Neon PostgreSQL**：在 [neon.tech](https://neon.tech) 建立免費 project，取得連線字串（格式：`postgresql+psycopg2://user:pass@host.neon.tech/dbname?sslmode=require`）。首次本地執行 `DATABASE_URL=<neon_url> python -m src.main` 以自動建表。

2. **GitHub Secrets**（`Settings → Secrets → Actions`）：

   | Secret 名稱 | 內容 |
   |---|---|
   | `DATABASE_URL` | Neon 連線字串 |
   | `GEMINI_API_KEY` | Google AI Studio Key |
   | `DISCORD_WEBHOOK_URL` | Discord Webhook URL（選用） |
   | `ALERT_VFM_THRESHOLD` | VFM 推播閾值，預設 500（選用） |
   | `SHOPEE_APP_ID` | 蝦皮聯盟行銷 AppID（選用；設了才會在 CI 上跑蝦皮，[怎麼申請](#申請蝦皮聯盟行銷-open-api)） |
   | `SHOPEE_APP_SECRET` | 蝦皮聯盟行銷 Secret（選用） |

3. **GitHub Actions**：push `.github/workflows/scraper.yml` 後自動啟用。可到 Actions 頁面手動 dispatch 測試。每天執行完畢後 Discord 會收到 heartbeat 通知——**某來源爬取失敗時會顯示 ⛔ 與失敗原因，不會偽裝成「0 筆」**。

4. **本機蝦皮排程**（在 Affiliate API 通過前的資料來源）：先跑一次
   `SHOPEE_HEADLESS=false python -m src.main --source shopee` 手動登入建立 session，
   再依 `scripts/run_local_shopee.ps1` 檔頭註解註冊 Windows 工作排程器。
   `.env` 的 `DATABASE_URL` 要指向 Neon，本機跑的結果才會進到雲端 Dashboard。

5. **Streamlit Community Cloud**：連結 GitHub repo → Main file path 設為 `streamlit_app.py` → Secrets 貼入：
   ```toml
   DATABASE_URL = "postgresql+psycopg2://...?sslmode=require"
   SAINTECH_URL = "https://channel.saintechtw.com/"   # 選用，頁尾頻道連結
   ```
   Dashboard sidebar 會顯示「🔄 資料庫最後更新時間」確認資料新鮮度。

   兩個平台限制先知道為妙：**不支援自訂網域**（只能用 `你的app名.streamlit.app`，用 CNAME 指過去會因憑證不符而失敗），且**超過約 12 小時無人造訪就會休眠**，下次開啟需等它重新啟動。部署後 repo 與 branch 即固定，設定頁只有 General／Sharing／Secrets，要換分支只能重新部署一個 app。

---

## 執行指令（本地開發）

### 手動跑一次完整爬蟲 + 估價

```bash
python -m src.main
```

執行完畢後會在根目錄產生 `valuation_report.csv`，並在終端機印出前 15 名高 VFM 機器。

只想重新修復/評分/推播既有資料，不重新爬蟲（例如測試 Discord 通知，避免浪費 LLM 額度重新解析新項目）：

```bash
python -m src.main --skip-scrape
```

### 確認目前連的是哪個資料庫

```bash
python -m src.scripts.check_db
```

印出 `DATABASE_URL` 指向的主機（密碼會遮罩）、是 SQLite 還是 Neon、各來源筆數與最後更新時間。**設定本機蝦皮排程前務必先跑這個**——若顯示 SQLite，爬到的資料只會進本機檔案，不會出現在雲端 Dashboard。

### 驗證蝦皮 Affiliate API 覆蓋率

申請流程與門檻見上方「[申請蝦皮聯盟行銷 Open API](#申請蝦皮聯盟行銷-open-api)」。拿到金鑰後：

```bash
python -m src.scripts.probe_shopee_affiliate
python -m src.scripts.probe_shopee_affiliate --keyword "MacBook Pro 二手" --pages 2 --dump nodes.json
```

`--dump` 會把原始回應寫成 JSON，方便檢查欄位。若 GraphQL 回報欄位錯誤，代表 TW schema 與 `_build_query()` 請求的欄位有出入，縮減 `src/scrapers/shopee_api.py` 裡的欄位清單即可。

### 測試 Discord 推播

```bash
python -m src.scripts.trigger_test   # 強制標記資料庫中一筆已解析商品，讓它下次跑一定觸發推播
python -m src.main --skip-scrape     # 不爬蟲，直接修復＋評分＋送出通知
```

需要先在 `.env` 設定 `DISCORD_WEBHOOK_URL`（Discord 頻道設定 → 整合 → Webhook → 複製 Webhook URL）。

### 啟動 Streamlit Dashboard

```bash
streamlit run streamlit_app.py
```

必須從根目錄的 `streamlit_app.py` 進入。直接跑 `streamlit run src/dashboard.py` 會因為 repo 根目錄不在 `sys.path` 而拋 `ModuleNotFoundError: No module named 'src'`。

Dashboard 直連 `DATABASE_URL` 指定的資料庫（本地 SQLite 或 Neon PostgreSQL），不經過 FastAPI。

物件以響應式卡片呈現：視窗 ≥1800px 四欄、≥1200px 三欄、≥700px 兩欄、手機單欄。呈現規範見 [`.spec/specs/api/spec.md`](.spec/specs/api/spec.md)。

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
