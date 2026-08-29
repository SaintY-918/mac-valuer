# 日常操作

所有指令都要在專案根目錄執行。除了兩支 session 工具之外，一律使用虛擬環境的 Python：

```powershell
.\venv\Scripts\python.exe -m src.main
```

`export_shopee_session` 與 `restore_shopee_session` 只用標準函式庫，用哪個 Python 都行。

---

## 執行管線

```bash
python -m src.main                          # 全部來源
python -m src.main --source ptt,carousell   # 指定來源，逗號分隔
python -m src.main --skip-scrape            # 不爬蟲，只重新解析＋評分＋推播
python -m src.main --dry-run                # 只印出通過爬蟲過濾的物件
```

`--skip-scrape` 適合在修改解析邏輯後回填既有資料，不會觸發驗證碼、也不會抓到新物件。

執行完會在根目錄產生 `valuation_report.csv`。

---

## 診斷工具

| 指令 | 用途 |
|---|---|
| `python -m src.scripts.check_db` | 目前連的是哪個資料庫、各來源筆數、最後更新時間（密碼遮罩） |
| `python -m src.scripts.repair_specs` | 掃描不可能的規格值（試算，`--apply` 才寫入） |
| `python -m src.scripts.trigger_test` | 強制標記一筆物件，讓下次執行必定觸發推播 |
| `python -m src.scripts.suppress_initial_burst` | 首次部署前抑制歷史物件的爆量推播 |

### 測試 Discord 推播

```bash
python -m src.scripts.trigger_test
python -m src.main --skip-scrape
```

需要先設定 `DISCORD_WEBHOOK_URL`（Discord 頻道設定 → 整合 → Webhook）。

---

## 啟動介面

```bash
streamlit run streamlit_app.py              # Dashboard
uvicorn api.main:app --reload --port 8000   # FastAPI
```

**必須從根目錄的 `streamlit_app.py` 進入。** 直接跑 `streamlit run src/dashboard.py`
會因為 repo 根目錄不在 `sys.path` 而拋 `ModuleNotFoundError: No module named 'src'`。

Dashboard 直連 `DATABASE_URL`，不經過 FastAPI。物件以響應式卡片呈現：
視窗 ≥1800px 四欄、≥1200px 三欄、≥700px 兩欄、手機單欄。

---

## 蝦皮

### 申請聯盟行銷 Open API

免費。方向是相反的——有人透過你的連結下單，蝦皮付你佣金。

**先確認申請的是哪一個**，兩者名字很像但完全不同：

| | 蝦皮 Open Platform（賣家 API） | **蝦皮聯盟行銷 Open API** |
|---|---|---|
| 網域 | `partner.shopeemobile.com` | `open-api.affiliate.shopee.tw` |
| 對象 | 商城賣家、ERP 供應商 | 分潤計畫推廣夥伴 |
| 本專案要的 | ❌ 資格不符 | ✅ 這個 |

**流程**

1. [affiliate.shopee.tw](https://affiliate.shopee.tw/) 點「開始使用」，用既有蝦皮帳號登入
2. 填個人資料與**媒體資料**（需提供社群帳號或網站）
3. 人工審核約 **2~5 個工作天**
4. 通過後前往 `https://affiliate.shopee.tw/open_api` — **選單裡沒有這一項，要直接打網址**
5. 該頁的「聯繫我們」申請開通 Open API 權限

> ⚠️ **審核通過 ≠ 有 API 權限。** 實測後台顯示「您目前無權限申請蝦皮分潤計畫之
> Open API 金鑰。請聯繫我們以開通權限」，且「申請API金鑰」按鈕為停用狀態。
> 側邊欄的 Product Feed 同樣顯示「尚無數據」，兩條官方路徑都需對方開通。

**門檻**：任一社群平台至少 300 位好友／追蹤者，或網站具一定流量。

**拿到金鑰後先驗證覆蓋率再切換**——聯盟目錄只收錄加入計畫的賣場，
二手個人賣家是否涵蓋其中必須實測：

```bash
python -m src.scripts.probe_shopee_affiliate
python -m src.scripts.probe_shopee_affiliate --keyword "MacBook Pro 二手" --pages 2 --dump nodes.json
```

腳本會直接給出「值得切換」或「覆蓋率不足，維持瀏覽器爬蟲」的判斷。

### session 過期

蝦皮會週期性要求重新驗證。headless 排程遇到驗證碼必定失敗，需手動處理一次：

```powershell
$env:SHOPEE_HEADLESS="false"
.\venv\Scripts\python.exe -m src.main --source shopee
```

完成滑動驗證後回終端機按 Enter。

### 重驗 CI 可行性

蝦皮政策若有變動，可手動觸發 `.github/workflows/shopee-ci-test.yml` 重新測試。
它不寫入資料庫、不呼叫 LLM，只回報 runner 上抓不抓得到商品。

需要 `SHOPEE_STATE_B64` secret：

```bash
python -m src.scripts.export_shopee_session --clip   # 直接複製到剪貼簿
```

**不要從終端機圈選複製**——3 KB 的字串在會折行的主控台圈選必定出錯，實測過兩次。

---

## 外部服務的額度與限制

本專案完全建構於免費方案，這是刻意的取捨。新增功能前先評估會不會撞到這些上限：

| 服務 | 限制 |
|---|---|
| Gemini | 15 RPM / 500 RPD（免費方案 Flash Lite） |
| Neon | 0.5 GB |
| Streamlit Cloud | 12 小時無人造訪即休眠；不支援自訂網域 |
| GitHub Secret | 單筆 48 KB（`SHOPEE_STATE_B64` 因此要 gzip） |

`GEMINI_RPM` 預設 13 而非 15，是刻意留餘裕——貼著上限跑，稍有抖動就會吃到 429。

### 更換 Gemini 模型

模型代號會被汰換，`gemini-3.5-flash-lite` 終將下架，屆時 pipeline 會直接失敗。
代號沒有寫死在程式裡，改環境變數即可，**不需要動任何程式碼**：

1. 本機改 `.env` 的 `GEMINI_MODEL`
2. GitHub Actions 改 repository secret 或 workflow 的 `env`
3. Streamlit Cloud 改 app 設定裡的 secrets

換之前先確認新模型的額度——非 Lite 的 Flash 系列免費方案只有 20 RPD，
不足以支撐每日全量解析。

---

## 提交前的個資檢查

repo 是公開的，提交出去就收不回來。檢查清單見 [`CLAUDE.md`](../CLAUDE.md)，
機械式的部分可以用 grep 掃：

```bash
# 執行前把 <> 換成要搜尋的實際值。
# 不要把真實 email 或 IP 留在這份文件裡 —— 這份文件本身也是公開的。
git ls-files | xargs grep -lniE '<你的email>|<內網IP前綴>|<你的使用者名稱>' 2>/dev/null
```

掃不到不等於乾淨——截圖、日誌貼上前要用眼睛看過。

---

## 疑難排解

| 症狀 | 原因 |
|---|---|
| `ModuleNotFoundError: No module named 'pandas'` | 用了系統 Python，要用 `.\venv\Scripts\python.exe` |
| `ModuleNotFoundError: No module named 'src'` | 不在專案根目錄，或直接跑了 `src/dashboard.py` |
| Discord 顯示 ⛔ 某來源失敗 | 訊息裡有失敗原因；蝦皮多半是 session 過期 |
| 排程 `LastTaskResult` 非 0 | 看 `logs/scrape_YYYY-MM-DD.log` |
| Neon 連線 timeout | 網路可能擋 5432 埠，換行動網路測試 |
| Dashboard 顯示舊版 | Streamlit Cloud 快取，用無痕視窗開 |

---

## Streamlit Cloud 上訪客實際看得到什麼

實測方式：用未登入的瀏覽器開 [mac-valuer.streamlit.app](https://mac-valuer.streamlit.app)，
而不是憑印象判斷。

| 元件 | 擁有者 | 訪客 |
|---|---|---|
| Share、星號、編輯鉛筆 | ✅ | ❌ |
| 右下 **Manage app** | ✅ | ❌ |
| **Deploy** 按鈕 | ✅（本機亦有） | ❌ |
| 右上 **⋮** 選單 | ✅ | ✅ → 已用 `toolbarMode = "minimal"` 關閉 |
| 右下角 Streamlit 徽章 | ✅ | ✅（無法用設定關閉） |

那個徽章是 Community Cloud 的標示，**留著**——免費代管的代價。

**側邊欄預設是展開的**（桌機）。手機寬度會自動收合，而且任何人手動收合後，
該瀏覽器會記住這個狀態。收合時只剩一個箭頭符號，所以加了「篩選條件」標籤——
Streamlit 沒有對應設定，標籤是用 CSS 掛在 `[data-testid="stExpandSidebarButton"]` 上的，
只在收合時存在，正是需要它的時候。
