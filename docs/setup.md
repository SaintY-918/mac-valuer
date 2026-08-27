# 安裝與部署

## 本機環境

需要 Python 3.11 以上。

```bash
pip install -r requirements.txt
playwright install chromium          # PTT 爬蟲需要
python -m camoufox fetch             # 蝦皮瀏覽器爬蟲需要（僅本機）
```

複製環境變數範本：

```bash
cp .env.example .env
```

### 必填

| 變數 | 說明 |
|---|---|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) 取得 |
| `DATABASE_URL` | 本機可用預設的 SQLite；要與雲端共用資料則填 Neon 連線字串 |

### 選填

| 變數 | 預設 | 說明 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | 空 | 未設定則推播自動停用，pipeline 不報錯 |
| `ALERT_VFM_THRESHOLD` | `500` | 高於此分數才推播 |
| `GEMINI_RPM` | `13` | 每分鐘 LLM 請求上限。免費方案為 15，預設留餘裕 |
| `STALE_DAYS` | `14` | 連續幾天未再出現才標記為下架 |
| `SHOPEE_SKIP_DETAILS` | `true` | 蝦皮精簡模式，只讀搜尋頁 |
| `CAROUSELL_MAX_ITEMS` | `30` | 旋轉拍賣每次取最新幾筆 |
| `SAINTECH_URL` | 空 | 頁尾頻道連結，未填則不顯示 |

完整清單見 [`.env.example`](../.env.example)。

**確認資料庫連的是哪裡**（密碼會遮罩）：

```bash
python -m src.scripts.check_db
```

---

## 雲端部署

```
GitHub Actions（每日 UTC 18:00 ＝ 台灣 02:00）
  └─ python -m src.main --source ptt,carousell
       └─ 寫入 ─┐
                ├─► Neon PostgreSQL（免費 0.5 GB）
本機 Windows 排程 ┘                    ▲
  └─ scripts/run_local_shopee.ps1      │
       └─ python -m src.main --source shopee
                                       │
              Streamlit Community Cloud（免費、公開）
              直連 DBManager，不經過 FastAPI
```

### 為什麼蝦皮要分開跑

**蝦皮的瀏覽器爬蟲無法在 GitHub Actions 上運作。** 三個原因裡有兩個其實可解：

| # | 原因 | 可否在 CI 解決 |
|---|---|---|
| 1 | 登入 session 存在本機，未帶到 runner | ✅ 可（存成 secret） |
| 2 | camoufox 的 Firefox 核心未安裝 | ✅ 可（`camoufox fetch`） |
| 3 | **蝦皮判定 runner 的 IP 段為爬蟲** | ❌ **無解** |

第 3 點經實測確認：補齊前兩項後，runner 帶著 22 個有效 cookie 仍被導向
`shopee.tw/verify/captcha?...&scene=crawler_item`。**換一台免費的機房主機只是換一個會被擋的 IP。**

詳見 [`docs/decisions.md`](decisions.md) 第 1 則。

### 步驟

**1. Neon PostgreSQL**

到 [neon.tech](https://neon.tech) 建立免費 project，取得連線字串。
Neon 預設給的是 `postgresql://`，本專案用 psycopg2，需改成 `postgresql+psycopg2://`，
並保留 `?sslmode=require`。

**2. GitHub Secrets**（`Settings → Secrets and variables → Actions`）

| Secret | 內容 |
|---|---|
| `DATABASE_URL` | Neon 連線字串 |
| `GEMINI_API_KEY` | Google AI Studio Key |
| `DISCORD_WEBHOOK_URL` | 選用 |
| `ALERT_VFM_THRESHOLD` | 選用，預設 500 |
| `SHOPEE_APP_ID` / `SHOPEE_APP_SECRET` | 選用；設了才會在 CI 上跑蝦皮 |

**3. GitHub Actions**

push `.github/workflows/scraper.yml` 後自動啟用。
每天執行完 Discord 會收到 heartbeat——**某來源失敗時顯示 ⛔ 與原因，不會偽裝成「0 筆」**。

**4. 本機蝦皮排程**

先手動登入建立 session：

```powershell
$env:SHOPEE_HEADLESS="false"
.\venv\Scripts\python.exe -m src.main --source shopee
```

會開啟瀏覽器視窗，完成登入或驗證後回終端機按 Enter。
接著依 [`scripts/run_local_shopee.ps1`](../scripts/run_local_shopee.ps1) 檔頭註解註冊 Windows 工作排程器。

`.env` 的 `DATABASE_URL` 必須指向 Neon，本機跑的結果才會進到雲端 Dashboard。

**5. Streamlit Community Cloud**

連結 GitHub repo → Main file path 設為 `streamlit_app.py` → Secrets：

```toml
DATABASE_URL = "postgresql+psycopg2://...?sslmode=require"
SAINTECH_URL = "https://channel.saintechtw.com/"   # 選用
```

平台限制先知道為妙：

- **不支援自訂網域**（只能用 `你的app名.streamlit.app`，用 CNAME 指過去會因憑證不符而失敗）
- **超過約 12 小時無人造訪會休眠**，下次開啟需等它重新啟動
- **branch 在部署時固定**，設定頁只有 General／Sharing／Secrets，要換分支只能重新部署一個 app
