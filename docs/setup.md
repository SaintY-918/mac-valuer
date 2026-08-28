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
  └─ python -m src.main --source ptt
       └─ 寫入 ─┐
                ├─► Neon PostgreSQL（免費 0.5 GB）
本機 Windows 排程 ┘                    ▲
  └─ scripts/run_local_scrape.ps1      │
       └─ python -m src.main --source carousell,shopee
                                       │
              Streamlit Community Cloud（免費、公開）
              直連 DBManager，不經過 FastAPI
```

### 為什麼蝦皮與旋轉拍賣要分開跑

**蝦皮的瀏覽器爬蟲無法在 GitHub Actions 上運作。** 三個原因裡有兩個其實可解：

| # | 原因 | 可否在 CI 解決 |
|---|---|---|
| 1 | 登入 session 存在本機，未帶到 runner | ✅ 可（存成 secret） |
| 2 | camoufox 的 Firefox 核心未安裝 | ✅ 可（`camoufox fetch`） |
| 3 | **蝦皮判定 runner 的 IP 段為爬蟲** | ❌ **無解** |

第 3 點經實測確認：補齊前兩項後，runner 帶著 22 個有效 cookie 仍被導向
`shopee.tw/verify/captcha?...&scene=crawler_item`。**換一台免費的機房主機只是換一個會被擋的 IP。**

詳見 [`docs/decisions.md`](decisions.md) 第 1 則。

**旋轉拍賣同理，但原因更單純。** 它是純 HTTP、伺服器端渲染，一度被認為可以在 CI 跑——
實際上 runner 的每一個請求都是 403，五種標頭、五個路徑，只有 `robots.txt` 通得過。
同樣的請求從住宅 IP 全部 200。那是針對資料中心 IP 的封鎖政策，不是渲染方式的問題。

量測方式保留在 [`src/scripts/probe_carousell.py`](../src/scripts/probe_carousell.py)
與 `Carousell probe` workflow，日後想確認封鎖是否解除，執行一次即可，不必重新推理。

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

**4. 本機排程**

蝦皮與旋轉拍賣都擋資料中心 IP，GitHub runner 連不上（實測，見
[`docs/decisions.md`](decisions.md)），所以這兩個來源跑在自己的機器上。

先手動登入建立蝦皮 session：

```powershell
$env:SHOPEE_HEADLESS="false"
.\venv\Scripts\python.exe -m src.main --source shopee
```

會開啟瀏覽器視窗，完成登入或驗證後回終端機按 Enter。接著註冊排程：

```powershell
.\scripts\install_schedule.ps1
```

不需要系統管理員權限，也不會儲存密碼。要改時間或來源就加參數重跑一次
（`-At 03:00`、`-Sources "carousell,shopee"`），它會就地覆寫。

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

---

## 換一台電腦

repo 裡有的東西 `git clone` 就會回來。**不在 repo 裡的有四樣**——都是刻意的，
它們要嘛是機密、要嘛是機器本身的狀態：

| 要重建的 | 怎麼做 | 為什麼不在 repo |
|---|---|---|
| `venv` | `python -m venv venv`＋`venv\Scripts\pip install -r requirements.txt` | 平台相依，且體積大 |
| `.env` | 複製 `.env.example` 後填值 | 含金鑰與資料庫連線字串 |
| `shopee_state.json` | `SHOPEE_HEADLESS=false` 跑一次蝦皮並登入 | 是登入憑證，且會過期 |
| Windows 工作排程 | `.\scripts\install_schedule.ps1` | 是作業系統的狀態，不是檔案 |

完整順序：

```powershell
git clone https://github.com/SaintY-918/mac-valuer.git
cd mac-valuer
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt

copy .env.example .env          # 然後填入 DATABASE_URL / GEMINI_API_KEY / DISCORD_WEBHOOK_URL

$env:SHOPEE_HEADLESS="false"    # 開瀏覽器登入蝦皮一次
.\venv\Scripts\python.exe -m src.main --source shopee

.\scripts\install_schedule.ps1  # 註冊每日排程
Start-ScheduledTask -TaskName "mac-valuer-scrape"   # 立刻驗證一次
```

**資料不用搬。** 資料庫在 Neon，Dashboard 在 Streamlit Cloud，兩者都跟這台機器無關；
舊電腦的排程停掉（`.\scripts\install_schedule.ps1 -Uninstall`）就好。

**排程設定不要用手改。** 它由 `install_schedule.ps1` 定義。設定曾經寫在
`run_local_shopee.ps1`（現已更名）的檔頭註解裡、要人照著重打，結果註解和實際註冊的內容就
真的不一樣了——這種漂移不會有任何東西報錯。
