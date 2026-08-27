# mac-valuer — 大腦導航器

## 本專案使用 OpenSpec 管理規格

所有架構規格、資料模型、欄位定義、公式細節均存放於 `.spec/specs/` 下對應模組：

| 模組 | 規格文件 | 狀態 |
|------|---------|------|
| 爬蟲介面（Strategy Pattern）、PTT／蝦皮實作 | `.spec/specs/scraper/spec.md` | ✅ 已撰寫 |
| 資料庫策略、Schema、upsert 規則 | `.spec/specs/database/spec.md` | ⬜ 尚未撰寫 |
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
