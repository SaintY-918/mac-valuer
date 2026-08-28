# 變更紀錄

格式依循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。
決策的理由與被否決的方案記錄於 [`docs/decisions.md`](docs/decisions.md)。

---

## [未發布]

### 新增
- **測試套件（pytest）**，89 項，涵蓋規格抽取、晶片辨識、VFM 評分、瑕疵偵測、
  下架判定與各爬蟲的過濾規則。全部為純邏輯，**無金鑰、無資料庫亦可執行**。
- **CI 品質關卡**：每次 push 與 PR 自動執行 lint 與測試。
- **ruff linter**（`ruff.toml`），規則經篩選，只保留能抓出 bug 的項目。
- **文件一致性檢查**（`scripts/check_docs.py`），驗 env 變數、文件連結、spec 索引
  與評分常數，同時掛在 pytest 與 CI。
- **瀏覽器測試**（`tests/e2e/`，19 項）：自建固定測試資料庫，用 Playwright 驗卡片分數、
  瑕疵徽章、320／390／1440px 無橫向溢出、零 JS 錯誤。CI 獨立 job。
- **旋轉拍賣（Carousell）成為第三個資料來源**。純 HTTP、無反爬牆、資料位於
  schema.org JSON-LD，因此可在 GitHub Actions 上執行，不依賴本機。
  僅使用對方 `robots.txt` 允許的入口（sitemap 與 `/p/` 商品頁）。
- **蝦皮聯盟行銷 Open API 支援**。`SHOPEE_APP_ID` / `SHOPEE_APP_SECRET` 存在時
  自動改走官方 GraphQL，切換封裝在 scraper 內，`main.py` 無平台判斷。
  （金鑰尚未取得，見 decisions #2）
- **蝦皮精簡模式**（`SHOPEE_SKIP_DETAILS`，預設開啟）。跳過逐一開啟商品詳情頁，
  每次執行的請求數由約 33 降為 3。
- **瑕疵標註**。標題／成色／內文提到功能性瑕疵者，於卡片與 Discord 警報標示。
- **`--source` 支援逗號分隔**，CI 可一次執行 `ptt,carousell`。
- 工具腳本：`check_db`、`repair_specs`、`probe_shopee_affiliate`、
  `probe_shopee_browser`、`export_shopee_session`、`restore_shopee_session`。
- 規格文件：`scraper`、`api`、`llm-parser`、`score-engine`。

### 變更
- **移除死程式碼**：`src/processor/`（無人呼叫）、`src/parser/scraper.py`（已被取代的殘留）、
  `main.py: _is_invalid_chip()`。
- **Dashboard 改用 SainTech Design System**，並改為一列一筆的響應式卡片，
  取代原本 13 欄的 `st.dataframe`。手機上第一筆物件的位置由約 1150px 移至 297px。
- **Streamlit 主題移至 `.streamlit/config.toml`**。原本以注入 CSS 塗黑背景，
  但 Streamlit 本身仍在淺色主題，標題與所有 widget 都沒有跟著變。
- **下架判定改為時間老化**（`sweep_stale`，預設 14 天未再出現）。
  原本「本次執行沒看到就標記下架」對取樣式爬蟲不成立。
- **爬蟲失敗改為拋出例外**，Discord heartbeat 以 ⛔ 與原因呈現，
  不再與「今日無新物件」混為一談。
- **CI 只跑可在機房環境運作的來源**（PTT、Carousell）。
- **晶片基準分改用實測 Geekbench 6 多核並標註來源**；M5 系列先前為外推值。

### 修正
- **只寫在內文的功能性故障，整個系統看不到**。`find_defects()` 接受 `body_content`，
  Dashboard 與 Discord 也都傳了，但沒有任何讀取路徑會回傳這個欄位，兩邊拿到的都是
  `None`。PTT 賣家慣常把機況寫在內文。判定改到 pipeline 執行並存進 `parsed_json`。
- **spec 宣稱 M5 為外推值**，實際已換成 Geekbench 6 實測；並移除「新增世代要同時
  改前後端兩處」的舊指示——兩處早已合併為單一 `CHIP_BENCHMARKS`。
- **移除 `.env.example` 中無人讀取的 `API_HOST` / `API_PORT`**。
- **PTT 以「25k」形式標示的售價全部抽不到**。regex 裡有一個**實體倒退字元**（0x08）
  佔據了原本應為 `` 的位置，該樣式永遠不可能匹配。由 linter 掃出。
- **年份改用台灣時區計算**。伺服器跑在 UTC，跨年時會與台灣差一年，
  使全部物件的折舊年數偏移。
- **前後端 VFM 公式收斂為單一實作**。原本 125 筆中有 59 筆分數不同（最大差 55 分），
  7 筆落在 Discord 警報門檻兩側。修正後差異為 0。
- **晶片抽取不再寫死世代**。原本清單止於 M4，導致所有 M5 機種抽不到晶片而被丟棄，
  實際損失 9 筆（橫跨三個來源，且為單價最高的一群）。
- **補上晶片後重新推算年份**。年份缺失時評分回退 2020，使新機被當成六年前的機器，
  分數腰斬——同一款商品曾出現 432 與 204 兩種分數。
- **規格解析不再把 GPU 核心數當成記憶體**。`8C10G/8G/256G` 被讀成 RAM=10、SSD=8TB，
  而 8TB 會取得 SSD 加成、推高 VFM。
- **Gemini 呼叫加入節流**（預設 13 RPM）。原本以固定 `sleep(1)` 節流，
  等同最高 60 RPM，而免費方案上限為 15。429 的退避改為 65 秒以跨過分鐘窗口。
- **Dashboard 來源篩選補上 Carousell**，並修正「選兩個來源時完全不過濾」的問題。
- **前後端晶片基準表補齊 M1 Ultra / M2 Ultra**，原本網頁上會掉到預設值，同機差 5 倍。
- 移除殘留的 debug 程式碼（寫入不存在的 `scratch/` 目錄會直接中斷）。

### 安全性
- **提交身分改用 GitHub noreply 位址**，不再帶出真實 email（見 decisions #8）。
- `CLAUDE.md` 新增公開專案的個資檢查與外部免費服務的維護風險條款。

### 文件
- 記錄蝦皮聯盟行銷 Open API 的申請流程、門檻與**權限需另外開通**的實測結果。
- 記錄**蝦皮封鎖 GitHub runner 為實測結論**，非推論（`scene=crawler_item`）。
- 修正 `CLAUDE.md` 與 skill 文件中指向不存在路徑的 spec 連結。

---

## 2026-05-01 及之前

Dashboard 版面調整：標題斷行、分頁列在行動裝置上的排列、Material icon 與錨點圖示。
更早的開發歷程請見 git 紀錄。
