# mac-valuer — 大腦導航器

## 本專案使用 OpenSpec 管理規格

所有架構規格、資料模型、欄位定義、公式細節均存放於 `.spec/specs/` 下對應模組：

| 模組 | 規格文件 | 狀態 |
|------|---------|------|
| 爬蟲介面（Strategy Pattern）、PTT／蝦皮實作 | `.spec/specs/scraper/spec.md` | ✅ 已撰寫 |
| 資料庫策略、Schema、時間欄位語意、下架判定 | `.spec/specs/database/spec.md` | ✅ 已撰寫 |
| MacBookSpec、ScoringWeights、RawListing | `.spec/specs/data-models/spec.md` | ⬜ 尚未撰寫 |
| LLM Parser、Regex 抽取規則、資料修復 | `.spec/specs/llm-parser/spec.md` | ✅ 已撰寫 |
| VFM 公式、晶片基準分、晶片抽取規則 | `.spec/specs/score-engine/spec.md` | ✅ 已撰寫 |
| FastAPI endpoints、Dashboard 與 API 的關係、卡片呈現規範 | `.spec/specs/api/spec.md` | ✅ 已撰寫 |

尚未撰寫的模組以現有程式碼為準；動到該模組時應順手補上 spec。

輔助文件：`docs/ptt-scraper-logic.md`、`docs/skills/`（新增爬蟲、調整 VFM 公式的操作指引）。

---

## AI 必須遵守的強制規定

**在修改任何程式碼之前，必須先讀取對應的 spec 文件。**

- 修改資料庫相關程式碼 → 先讀 `database/spec.md`
- 修改爬蟲 → 先讀 `scraper/spec.md`
- 修改資料模型或權重 → 先讀 `data-models/spec.md`
- 修改 LLM 解析邏輯 → 先讀 `llm-parser/spec.md`
- 修改 VFM 公式或分數計算 → 先讀 `score-engine/spec.md`
- 修改 API 或 Dashboard → 先讀 `api/spec.md`

違反 spec 的實作等同於引入 bug，不得合併。

---

## 討論的結論必須寫進檔案

聊天記錄會被截斷、被關掉、被遺忘。**只要討論產生了結論，就必須落到檔案裡。**

觸發時機（符合任一即須執行）：

- 決定採用或**否決**某個方案
- 決定**延後**某件事（必須同時記下「什麼條件下重新評估」）
- 完成一個功能或修掉一個 bug
- 得到值得保留的實測結論（效能數據、平台限制、失敗原因）

作法：呼叫 `record-decision` skill，它定義了 `docs/decisions.md` 與 `CHANGELOG.md`
的格式與判準。**寫入前先跟使用者確認要記什麼**，不要自行認定討論已結束。

「決定不做」和它的理由，價值不亞於決定要做——沒記下來，下次會有人重提同一個
被否決過的方案，而且不知道當初為什麼否決。

---

## 這是公開專案 —— 個資檢查為強制項目

repo 公開在 GitHub，**任何提交都無法真正收回**（即使改寫歷史，舊 commit 仍可能經
SHA 存取、被 fork 或被快取）。因此檢查必須在提交**之前**。

### 每次提交前必須確認

| 類別 | 具體項目 |
|---|---|
| 憑證 | API 金鑰、資料庫連線字串、session／cookie、webhook URL |
| 身分 | 真實 email、真實姓名、電話、住址、平台帳號 ID |
| 網路 | 內網 IP、Wi-Fi SSID、主機名稱 |
| 路徑 | 含使用者名稱的絕對路徑（`C:\Users\<名字>\...`） |
| 截圖／日誌 | 貼進文件前先確認沒有夾帶上述任何一項 |

### 提交身分

使用 GitHub 的 noreply 位址，**不要用真實 email**：

```
70827317+SaintY-918@users.noreply.github.com
```

已在本 repo 的 `git config` 設定。曾有 27 筆 commit 誤用真實 gmail，見
[`docs/decisions.md`](docs/decisions.md) #8。

### 輸出到文件或終端機時

- 資料庫連線字串一律遮罩密碼（`check_db` 已內建）
- 貼上任何實際執行結果前，先掃過一遍
- 範例值用明顯的佔位符（`你的AppID`），不要用真實值改幾個字元

### 檢查方式

```bash
# 執行前把 <> 換成要搜尋的實際值。
# 不要把真實 email 或 IP 留在這份文件裡 —— 這份文件本身也是公開的。
git ls-files | xargs grep -lniE '<你的email>|<內網IP前綴>|<你的使用者名稱>' 2>/dev/null
```

**不確定就先問使用者，不要自行判斷「這個應該沒關係」。**

---

## 依賴外部免費服務 —— 維護風險須明示

本專案完全建構於免費方案，這是刻意的取捨，但有兩類風險必須寫進文件並定期檢視：

**額度限制**

| 服務 | 限制 |
|---|---|
| Gemini | 15 RPM / 500 RPD（免費方案） |
| Neon | 0.5 GB |
| Streamlit Cloud | 12 小時無人造訪即休眠；不支援自訂網域 |

新增功能時要評估是否會撞到這些上限。

**服務變動**

Gemini 的模型代號會汰換（`gemini-3.5-flash-lite` 終將下架，屆時 pipeline 會直接失敗）。
因此：

- 模型代號**必須可由環境變數覆寫**（`GEMINI_MODEL`），不得寫死在程式邏輯裡
- 更換方式須記載於 `docs/operations.md`
- 任何硬編碼的外部服務識別碼都應比照辦理
