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

## 疑難排解

| 症狀 | 原因 |
|---|---|
| `ModuleNotFoundError: No module named 'pandas'` | 用了系統 Python，要用 `.\venv\Scripts\python.exe` |
| `ModuleNotFoundError: No module named 'src'` | 不在專案根目錄，或直接跑了 `src/dashboard.py` |
| Discord 顯示 ⛔ 某來源失敗 | 訊息裡有失敗原因；蝦皮多半是 session 過期 |
| 排程 `LastTaskResult` 非 0 | 看 `logs/scrape_YYYY-MM-DD.log` |
| Neon 連線 timeout | 網路可能擋 5432 埠，換行動網路測試 |
| Dashboard 顯示舊版 | Streamlit Cloud 快取，用無痕視窗開 |
