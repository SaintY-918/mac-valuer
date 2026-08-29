# 變更紀錄

格式依循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。
決策的理由與被否決的方案記錄於 [`docs/decisions.md`](docs/decisions.md)。

---

## [未發布]

### 變更
- **`CLAUDE.md` 精簡為「一定要做／一定不能做」**（121 → 89 行）。原本混了規則、
  背景說明與操作指令三種東西，讀的人分不出哪些是鐵律。額度限制表與個資檢查指令
  移至 [`docs/operations.md`](docs/operations.md)；spec 對照表格式維持不動，
  `scripts/check_docs.py` 會解析它。
- **個資檢查明確涵蓋爬蟲抓到的第三方資料**。賣家的電話不會因為是抓來的就可以公開。

### 新增
- **`docs/operations.md` 的「更換 Gemini 模型」**。`CLAUDE.md` 原本聲稱此事記載於
  operations.md，但該段落並不存在——文件互指卻沒有實體，是最難發現的一種文件債。
- **`docs/operations.md` 的「外部服務的額度與限制」**，含 GitHub Secret 的 48 KB
  上限（`SHOPEE_STATE_B64` 需 gzip 的原因）。

### 移除
- **`.claude/` 移出版本控制**。25 個檔案中 24 個是工具的 vendored 設定，不是本專案
  的內容；`scripts/check_docs.py` 早已將其列入 `DOC_EXCLUDE`。本機檔案保留。

### 修正
- **CI 的 pytest job 一直在 collection 階段就失敗**，四個依賴不在手打的安裝清單裡
  （`feedparser`、`playwright`、`tabulate`、`google-genai`）。清單改為
  `pip install pytest ruff -r requirements.txt`，不再需要有人記得同步。
  原本不裝 requirements.txt 的理由是「會抓瀏覽器二進位檔」——`pip install` 並不會，
  抓的是 `playwright install`，這也正是 browser job 得額外寫那一行的原因。
- **decisions #7 的標題仍寫著「待處理」，內文狀態卻是「已修正」**。
- **`docs/decisions.md` 補上 #30–#32**：CI 依賴清單不再手寫、公開改用新 repo 而非
  強推、Streamlit app 的公開設定與 repo 公開是兩回事（含正確的 curl 驗證方式）。
- **decisions #8 改寫為「公開前的資料衛生稽核」**，記錄稽核範圍、發現的分類與
  處理方式。

---

## [0.1.0] — 2026-08-29

首次標記版本。此前的開發歷程都堆在「未發布」底下，讀者無從得知線上跑的是哪一段。

### 新增
- **`.spec/specs/data-models/spec.md`**：`MacBookSpec`、`ScoringWeights`、`RawListing`
  的欄位語意、為什麼全部可為空、以及跨模型的三條通則。此前是 CLAUDE.md 模組表中
  唯一未撰寫的一項——**沒有章節的模組不會被檢查**。
- **本機排程失敗會通知 Discord**（`run_local_scrape.ps1`）。通知由 PowerShell 包裝層
  發出而非 python：2026-08-29 的排程死在 `import pandas`，通知器連載入都沒有，
  Discord 一個字都沒送出（見 decisions #28）。
- **快速失敗自動重試一次**。300 秒內死亡代表根本沒啟動，重試幾乎免費；更晚的失敗
  不重試，避免撞上排程的一小時上限。
- **pipeline 中止時送出 ⛔ 心跳**（`src/main.py` 的 `__main__` 補上 try/except）。
  原本任何在 Step 7 之前中止的錯誤都不會有任何訊息。
- **PTT 的規格章節**（`.spec/specs/scraper/spec.md`）。此模組原本完全沒有 spec，
  而沒有章節的模組不會被檢查。
- `PAGE_SIZE` 環境變數，讓瀏覽器測試能用少量測資翻頁。
- 頁尾新增 GitHub 原始碼連結。
- **`src/scripts/revalidate_chips.py`**：以現行規則重新檢驗已存的晶片，
  清掉不再通過的（例如被誤判為 Apple M5 的 2016 年 Intel 機）。
- **`scripts/install_schedule.ps1`**：本機排程改由腳本定義而非檔頭註解。
  可重複執行、不需管理員權限、不儲存密碼，並在註冊前檢查 venv／`.env`／蝦皮 session。
- **`docs/setup.md` 換電腦章節**：列出四樣不在 repo 裡的東西與重建方式。
- **`.spec/specs/database/spec.md`**：Schema、三個時間欄位的分工、下架判定為何以年齡
  而非集合為準、讀取路徑的欄位規則。
- **`MAX_REPAIR_CALLS_PER_RUN`**（預設 50）與解析指紋：文字沒變過的物件不再重複詢問。
- **測試套件（pytest）**，涵蓋規格抽取、晶片辨識、VFM 評分、瑕疵偵測、
  下架判定與各爬蟲的過濾規則。全部為純邏輯，**無金鑰、無資料庫亦可執行**。
- **CI 品質關卡**：每次 push 與 PR 自動執行 lint 與測試。
- **ruff linter**（`ruff.toml`），規則經篩選，只保留能抓出 bug 的項目。
- **文件一致性檢查**（`scripts/check_docs.py`），驗 env 變數、文件連結、spec 索引
  與評分常數，同時掛在 pytest 與 CI。
- **瀏覽器測試**（`tests/e2e/`）：自建固定測試資料庫，用 Playwright 驗卡片分數、
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
- **PTT 爬蟲改為純 HTTP，不再開瀏覽器**。內文抽取與 Playwright 的 `inner_text()`
  在六篇實際文章上比對，正規化空白後完全相同——瀏覽器沒有換到任何東西，卻讓 CI
  多了一個會被忘記的安裝步驟。`scraper.yml` 因此不再安裝 Chromium。
- **VFM 分數構成改寫**：刪掉長條圖與規則表，只留下真實物件的逐步算式（每一步標明
  為何套用該乘數）與完整的晶片基準分。高度 924px → 673px，並移除 plotly 相依。
- **色帶與中位數標明「全站基準」**。閾值本來就取自全部在售物件而非篩選後的清單，
  但畫面上沒說，讀者無從判斷篩選會不會改變標準。已加測試把這個性質釘住。
- README 補上桌機與手機截圖。
- **網站更名為「Mac 好價雷達」**。原本的「估價」宣稱了一個沒有實作的功能——
  系統不接受「我有一台 X，值多少」這種輸入，它是蒐集在售物件並依 CP 值排序。
  錯誤畫面的標題原為「二手 MacBook 智慧估價系統」，問題更嚴重。
  名稱同時從 MacBook 放寬到 **Mac**，日後納入 Mac mini 等機種不必再改名。
- 標題上方小字改為列出實際來源（由 `SOURCE_LABELS` 推導）與更新頻率。
- **收合的側邊欄加上「篩選條件」標籤**——原本只有一個箭頭符號，沒有任何字說明它會開出什麼。
- **`toolbarMode = "minimal"`**：隱藏開發者工具列。實測未登入訪客看得到的只有 ⋮ 選單，
  Share／星號／編輯／Manage app 都是擁有者專屬。
- **列表改版**：卡片改為髮絲線分隔的資料列；**機型當大字、賣家原標題退為副標**；
  分數單位標在清單上方一次並附中位數；淺色主題；字體改用 Inter，數字不再使用等寬字。
- **配色與字體不再綁 SainTech 品牌**——那組藍與 Archivo 900 是從別的專案沿用的。
- **旋轉拍賣改在本機執行**，CI 只跑 PTT。Carousell 對 GitHub runner 的每個請求都是 403
  （五種標頭 × 五個路徑，僅 `robots.txt` 通過），留在 CI 只會每晚發一則假失敗。
- `run_local_shopee.ps1` → `run_local_scrape.ps1`，來源改由 `-Sources` 參數決定；
  排程工作更名為 `mac-valuer-scrape`，安裝腳本會自動移除舊的註冊。
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
- **MacBook Neo 的物件整批被丟棄——第二種成因**。賣家寫「MacBook Neo 8G/256GB」
  就停筆，因為該機型至今只出過一種晶片，寫出來對他們是贅字；而抽取器抽不到晶片
  就丟掉。一次執行有 **7 筆**（跨三個賣家）因此消失。新增機型→晶片對照，
  **僅在完全抽不到時套用**，標題明寫的晶片一律優先——這個順序是它與猜測的分界。
  對照本身標明是「有到期日的產品事實」：下一代 Neo 上市即失效，且註明該改哪裡。
- **CI 的 PTT 連續三晚完全沒有抓到資料**。`ba2d143` 以「camoufox has no browser
  installed」為由刪掉 workflow 的 `playwright install`——該理由屬於當時被移出 CI
  的蝦皮，未對留在 CI 的 PTT 檢查，而 PTT 每篇文章都在開 Chromium。
  自 2026-08-26 起每晚失敗於 `BrowserType.launch`（見 decisions #29）。
- **本機排程失敗時完全靜音**。2026-08-29 智慧型應用程式控制封鎖了 pandas 的
  `timezones.cp313-win_amd64.pyd`，執行在五秒內死於 `src/main.py:9`，
  而通知器在第 15 行才 import、心跳在 Step 7 才送出、`__main__` 沒有 try/except、
  PowerShell 的結束碼沒有人讀。四層都沒接住（見 decisions #28）。
- **PTT 的 feed 取不到內容時回傳空 list**，在心跳上顯示為「0 筆」，與真正平靜的
  一晚無法區分。feedparser 對網路失敗的回報方式是「沒有 entries 的物件」而非例外。
  改為拋出例外。
- **旋轉拍賣的 spec 仍宣稱可在 GitHub Actions 上執行**，且「與 PTT 同級穩定」。
  兩半都已不成立：旋轉拍賣在 runner 上每個請求都是 403（8/28 實測），
  而 PTT 當時正在開瀏覽器。程式碼的 docstring 與 CHANGELOG 早已更新，只有 spec 沒有。
- **「下一頁」完全沒有作用**。頁碼下拉選單同時有 `key` 與 `index=`，
  而 keyed widget 會還原自己存的值並忽略 `index`——按鈕把頁碼設成 2 之後，
  下拉選單回報 1、判定不一致，又把它設回 1。按鈕的動作被旁邊的元件抵銷。
- **每次互動都要 4.5 秒**。每次 rerun 都對 Neon 查四次（篩選清單、未篩選基準、
  今日新增、最後更新時間），每趟約 1 秒。翻頁不改變任何篩選條件，
  卻要付全額。加上 5 分鐘快取後 **5.0s → 0.3s**。
- **展開區的圖表在淺色主題上是一塊黑**（`plot_bgcolor` 寫死深色）；公式的
  `st.code` 尾端被裁掉；1:1 分欄讓表頭全被拆成兩行。
- **規則表裡的晶片基準分是手抄的，且已過期**——缺 M5 與 A18 Pro。已刪除該表，
  基準分只從 `CHIP_BENCHMARKS` 讀。
- **基準分表按分數排序、固定高度 210px**，可見的永遠是最罕見的五顆晶片
  （M5 Max／M5 Pro／M2 Ultra／M4 Max／M1 Ultra），M1～M4 反而要捲動。
  改為依世代排列的完整網格。
- **篩選可以選出不存在的組合**（例如 MacBook Pro + 15 吋）。Apple 沒出過 15 吋 Pro
  或 14 吋 Air，選了會得到空清單，讀起來像沒貨而不是「這台機器不存在」。
  螢幕尺寸選項改為跟隨機型；切換機型時若原選尺寸失效會自動清掉。
- **篩選器少了 MacBook Neo**。`ModelSeries` 沒有這個值，pydantic 會拒絕，
  所以解析器根本無法描述一台 Neo。已加入 enum、篩選選單與查詢對照表。
- **RAM／SSD 篩選上限過低**（64 GB／2 TB），而解析器早已接受 128 GB／8 TB。
  兩份清單改為由 `mac_spec.VALID_RAM_GB` / `VALID_SSD_GB` 推導。
- **兩處「資料來源」都漏了旋轉拍賣**。改為由 `SOURCE_LABELS` 推導。
- **螢幕尺寸顯示不一致**：同一種機型會寫成 13 / 13.3 / 13.6，資料中甚至有 Apple
  從未出過的 15.6 吋。改由 `nominal_inches()` 統一為 13 / 14 / 15 / 16，
  且與計分用的形態加成同源。
- **桌機版價格離標題太遠**：全寬時中間有數百像素空白，眼睛得走完整段才能把兩者配起來。
  內容區加上 1120px 上限。
- **Intel Core m5 被判為 Apple M5**。2016 年 12 吋 Retina MacBook 套上 M5 的
  17,933 基準分後得到 1060 分、**全站第一**，並超過撿漏警報門檻。
  新增 Intel 標記過濾與「年份早於 2020 則否決晶片」規則。
- **中文標題的晶片一律抽不到**。`\b` 在 Unicode 下把中文視為 word char，
  「M2晶片」因此完全不匹配——而中文賣家正是這樣寫。實測 6 筆因此只能倚賴 LLM。
- **Gemini 的每日額度 429 被當成每分鐘額度重試**，每次白睡 65 秒。實測一次執行睡掉
  18 分鐘、只處理到第 19 筆，最後撞上排程的一小時上限，評分與推播全部沒跑到。
  改為判讀錯誤中的 `quotaId`：每日額度直接中止解析階段，讓 pipeline 走完後續步驟。
- **額度用盡在 Discord 顯示為「爬取失敗」**，指向錯誤的地方。改為獨立一行的 🪫 提示。
- **被晶片過濾丟棄的物件不會記錄解析指紋**，導致每次執行都重新問一次同樣的問題。
- **排程的 PowerShell 視窗會被誤關**，把跑到一半的執行砍掉（2026-08-27、08-28 兩次，
  結束碼 `0xC000013A`）。改為 `-WindowStyle Hidden`。
- **python 輸出未即時寫入 log**，行程被砍時整段緩衝區消失，事後查不到跑到哪裡。加上 `-u`。
- **解析迴圈把沒看過的物件蓋成「剛看到」**，導致 186 筆中有 121 筆永遠不會被
  `sweep_stale()` 判定下架。解析改用新的 `update_parsed()`，不碰 `last_seen`。
- **`location == "未知"` 是自我循環的修復條件**：判定需要修復，修完又設回 `"未知"`。
  69 筆（每晚 LLM 呼叫的 57%）卡在這個迴圈裡，且保證修不好。已移除該條件。
- **解析迴圈忽略 `--source`**：本機只跑蝦皮的排程會改寫 CI 負責的 PTT 與旋轉拍賣資料。
- **`get_all_deals()` 沒有回傳 `source`**——同一類遺漏的第三次。
- **MacBook Neo（A18 Pro）整批被丟棄**。晶片抽取只認 `M` 開頭，A 系列抽不到晶片，
  而無晶片的物件會被 `_INVALID_CHIPS` 直接丟掉——與當初 M5 被靜默丟失同一個成因。
  已支援 A 系列、補上基準分（Geekbench 6 多核 8,668），並歸入 `air13` 形態。
- **只寫在內文的功能性故障，整個系統看不到**。`find_defects()` 接受 `body_content`，
  Dashboard 與 Discord 也都傳了，但沒有任何讀取路徑會回傳這個欄位，兩邊拿到的都是
  `None`。PTT 賣家慣常把機況寫在內文。判定改到 pipeline 執行並存進 `parsed_json`。
- **spec 宣稱 M5 為外推值**，實際已換成 Geekbench 6 實測；並移除「新增世代要同時
  改前後端兩處」的舊指示——兩處早已合併為單一 `CHIP_BENCHMARKS`。
- **移除 `.env.example` 中無人讀取的 `API_HOST` / `API_PORT`**。
- **PTT 以「25k」形式標示的售價全部抽不到**。regex 裡有一個**實體倒退字元**（0x08）
  佔據了原本應為 `\b` 的位置，該樣式永遠不可能匹配。由 linter 掃出。
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
- **CHANGELOG 與 README 不再手抄測試數量**。數字寫進散文就得靠人維護，
  而它已經從 89 漂到 190（e2e 從 19 漂到 23）。**刪掉數字而不是更新數字**——
  更新只會讓它下次再漂一遍。
- 記錄蝦皮聯盟行銷 Open API 的申請流程、門檻與**權限需另外開通**的實測結果。
- 記錄**蝦皮封鎖 GitHub runner 為實測結論**，非推論（`scene=crawler_item`）。
- 修正 `CLAUDE.md` 與 skill 文件中指向不存在路徑的 spec 連結。

---

## 2026-05-01 及之前

Dashboard 版面調整：標題斷行、分頁列在行動裝置上的排列、Material icon 與錨點圖示。
更早的開發歷程請見 git 紀錄。
