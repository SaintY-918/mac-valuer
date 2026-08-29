# mac-valuer — 開發守則

## 規格文件

架構、資料模型、欄位定義、公式細節都在 `.spec/specs/`：

| 模組 | 規格文件 | 狀態 |
|------|---------|------|
| 爬蟲介面（Strategy Pattern）、PTT／蝦皮實作 | `.spec/specs/scraper/spec.md` | ✅ 已撰寫 |
| 資料庫策略、Schema、時間欄位語意、下架判定 | `.spec/specs/database/spec.md` | ✅ 已撰寫 |
| MacBookSpec、ScoringWeights、RawListing | `.spec/specs/data-models/spec.md` | ✅ 已撰寫 |
| LLM Parser、Regex 抽取規則、資料修復 | `.spec/specs/llm-parser/spec.md` | ✅ 已撰寫 |
| VFM 公式、晶片基準分、晶片抽取規則 | `.spec/specs/score-engine/spec.md` | ✅ 已撰寫 |
| FastAPI endpoints、Dashboard 與 API 的關係、卡片呈現規範 | `.spec/specs/api/spec.md` | ✅ 已撰寫 |

尚未撰寫的模組以現有程式碼為準；動到該模組時應順手補上 spec。

輔助文件：`docs/ptt-scraper-logic.md`、`docs/skills/`（新增爬蟲、調整 VFM 公式的操作指引）。
日常操作、外部服務的額度與限制見 [`docs/operations.md`](docs/operations.md)。

---

## 一定要做

### 改程式之前，先讀對應的 spec

| 要動的東西 | 先讀 |
|---|---|
| 資料庫 | `database/spec.md` |
| 爬蟲 | `scraper/spec.md` |
| 資料模型、權重 | `data-models/spec.md` |
| LLM 解析邏輯 | `llm-parser/spec.md` |
| VFM 公式、分數計算 | `score-engine/spec.md` |
| API、Dashboard | `api/spec.md` |

違反 spec 的實作等同於引入 bug，不得合併。

### 討論產生結論，就寫進檔案

聊天記錄會被截斷、被關掉、被遺忘。符合以下任一項就要寫：

- 決定採用或**否決**某個方案
- 決定**延後**某件事（同時記下「什麼條件下重新評估」）
- 完成一個功能或修掉一個 bug
- 得到值得保留的實測結論（效能數據、平台限制、失敗原因）

理由與被否決的方案寫進 [`docs/decisions.md`](docs/decisions.md)，
「改了什麼」寫進 [`CHANGELOG.md`](CHANGELOG.md)，格式照各自檔案裡的既有條目。
**寫入前先跟使用者確認要記什麼**，不要自行認定討論已結束。

「決定不做」和它的理由，價值不亞於決定要做——沒記下來，下次會有人重提同一個被否決過
的方案，而且不知道當初為什麼否決。

### 外部服務的識別碼，一律可由環境變數覆寫

模型代號會汰換（`GEMINI_MODEL`），寫死在程式邏輯裡等於埋一顆定時炸彈。
任何硬編碼的外部服務識別碼都比照辦理。

---

## 一定不能做

### 不能把個資帶進 commit

repo 是公開的，**提交出去就收不回來**，所以檢查必須在提交之前：

| 類別 | 具體項目 |
|---|---|
| 憑證 | API 金鑰、資料庫連線字串、session／cookie、webhook URL |
| 身分 | 真實 email、真實姓名、電話、住址、平台帳號 ID |
| 網路 | 內網 IP、Wi-Fi SSID、主機名稱 |
| 路徑 | 含使用者名稱的絕對路徑（`C:\Users\<名字>\...`） |
| 截圖／日誌 | 貼進文件前先確認沒有夾帶上述任何一項 |

爬到的第三方資料同樣算個資——賣家的電話不會因為是抓來的就可以公開。
檢查方式見 [`docs/operations.md`](docs/operations.md)。

### 不能用真實 email 提交

使用 GitHub 的 noreply 位址，已在本 repo 的 `git config` 設定。

### 不能拿真實值當範例

佔位符要一眼看得出是佔位符（`你的AppID`），不要拿真實值改幾個字元。
連線字串輸出一律遮罩密碼（`check_db` 已內建）。

---

**不確定就先問使用者，不要自行判斷「這個應該沒關係」。**
