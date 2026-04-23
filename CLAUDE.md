# mac-valuer — 大腦導航器

## 本專案使用 OpenSpec 管理規格

所有架構規格、資料模型、欄位定義、公式細節均存放於 `openspec/specs/` 下對應模組：

| 模組 | 規格文件 |
|------|---------|
| 資料庫策略、Schema、upsert 規則 | `openspec/specs/database/spec.md` |
| 爬蟲介面（Strategy Pattern）、PTT 實作 | `openspec/specs/scraper/spec.md` |
| MacBookSpec、ScoringWeights、RawListing | `openspec/specs/data-models/spec.md` |
| LLM Parser、Prompt schema、Regex fallback | `openspec/specs/llm-parser/spec.md` |
| VFM 公式、晶片基準分、形態加成、顏色閾值 | `openspec/specs/score-engine/spec.md` |
| FastAPI endpoints、Dashboard 與 API 的關係 | `openspec/specs/api/spec.md` |

開發歷程（Phase Plan、已知問題修復）：`openspec/changelog.md`

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
