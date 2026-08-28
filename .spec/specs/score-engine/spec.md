### 6.X Score Engine

**目標**：把 `MacBookSpec` 換算成 VFM（Value For Money）分數。

---

#### 6.X.1 公式

```
VFM = 晶片基準分 × 年份折舊 × RAM加成 × SSD加成 × 形態加成 ÷ 售價 × 1000
```

| 因子 | 規則 |
|---|---|
| 晶片基準分 | `src/utils/benchmark_db.py` 的 `CHIP_BENCHMARKS`；查無時預設 5000 |
| 年份折舊 | 每滿一年 ×0.9 |
| RAM 加成 | ≥16 GB 時套用（預設 ×1.25） |
| SSD 加成 | ≥1 TB 時套用（預設 ×1.1） |
| 形態加成 | Air 13"×1.00／Air 15"×1.08／Pro 13"×1.00／Pro 14"×1.18／Pro 16"×1.22 |

**公式只能有一份實作。** 所有 VFM 計算一律經由 `src/calculator/score_engine.py`：

| 呼叫端持有 | 使用 |
|---|---|
| `MacBookSpec` | `get_vfm_score(spec, weights)` |
| 原始 row／DataFrame 列 | `vfm_from_mapping(row, weights)` |

兩者共用 `adjusted_score()`，不得各自重算。Dashboard 曾自帶一份公式與基準表，
兩者漂移到 125 筆中有 59 筆分數不同（最大差 55 分），其中 7 筆落在
Discord 警報門檻的兩側——網頁顯示「優秀」卻永不推播，或會推播卻顯示為普通。

形態加成**必須同時看 `series` 與 `screen_size`**，判定邏輯集中於 `form_factor_key()`。
權重集中於 `ScoringWeights`，Dashboard 的滑桿預設值由其推導，不得另行硬編碼。

#### 6.X.2 晶片抽取（`main.py: force_extract_chip`）

- **不得寫死世代清單。** 原本的 `tiers = ["M4 MAX", ..., "M1"]` 停在 M4，導致
  **所有 M5 機種抽不到晶片**，而 `_INVALID_CHIPS` 過濾器會將無晶片的物件直接丟棄——
  實測資料庫中有 9 筆 M5 被靜默丟失，橫跨三個來源，且都是最新世代、單價最高的一群。
- 現行實作以 `(?<![A-Za-z0-9])([MA])(\d{1,2})\s*(PRO|MAX|ULTRA)?(?![A-Za-z0-9])`
  比對**兩個系列**，新世代只需補基準分，不必改抽取邏輯。
- **A 系列**：MacBook Neo（2026-03、$599、A18 Pro）是第一台採用 iPhone 晶片的 Mac，
  同一個「寫死清單」問題會再犯一次——只認 `M` 開頭時它抽不到晶片、被整筆丟棄。
  - 排序上 A 系列一律低於任何 M 系列，否則 `A18` 會在數字上壓過 `M5`。
  - 裸寫的 `A18` 正規化為 `A18 Pro`。Apple 只出過這一顆 A 系列 Mac 晶片，
    不正規化會落到 5000 預設分——賣家少打兩個字就砍半分數，與年份推論那個 bug 同類。
  - Apple 的機身型號是 `A` + **4 位數**（`A2338`、`A1706`），與 `A18` 不會相撞。
- **不得使用 `\b` 界定邊界。** Python 的 `\b` 是 Unicode-aware，中文屬於 word char，
  所以「M2晶片」在 2 與 晶 之間沒有邊界，**整個樣式匹配不到**——而中文賣家正是這樣寫。
  實測資料庫中有 6 筆因此完全依賴 LLM 才拿到晶片。改用
  `(?<![A-Za-z0-9])` 與 `(?![A-Za-z0-9])`。
- **Intel Core M 系列（m3／m5／m7）與 Apple 的 M3／M5 正面相撞。**
  標題含 `intel`、`core m`、`i5/i7/i9`，或**廣告時脈**（`1.2G`、`2.6GHz`）者一律不抽晶片。
  Apple 不以 GHz 行銷 Apple Silicon，而記憶體與容量寫作 `8G/256G`，不帶小數點。
  實測：2016 年 12 吋 Retina MacBook（Core m5 1.2G）被判為 Apple M5，
  套上 17,933 基準分後得到 **1060 分、全站第一**，且超過 500 的警報門檻。
- **年份可否決晶片**：Apple Silicon 自 2020 年 11 月起。解析出的年份早於 2020
  而晶片卻是 M 系列時，以年份為準並丟棄該筆——年份讀自整篇內容，
  晶片只匹配標題裡的四個字元。
- MacBook Neo 在形態上歸入 `air13`（13 吋無風扇入門機），不落進 `pro13` 這個預設分支。
- 同一標題出現多個晶片時取**最高階**（世代優先，同世代則 Ultra > Max > Pro > 無）。
- **含糊標題取低不取高**：賣家常堆關鍵字寫出不存在的「M1 Pro Max」。
  僅緊鄰世代的變體算數，因此判為 `M1 Pro`。低估 VFM 只是少一次機會，
  高估則會發出假的撿漏警報，後者代價大得多。

#### 6.X.3 基準分維護

- `CHIP_BENCHMARKS` 全表取自 **Geekbench 6 多核心**，來源逐條標註於
  `src/utils/benchmark_db.py`。**同一來源是硬性要求**：混用跑分平台會讓分數之間
  不可比較，連帶讓 VFM 失去意義。
- M5 系列已改為實測值（M5 17,933／M5 Pro 28,436／M5 Max 29,233），不再是外推。
- 新增世代只需改 `src/utils/benchmark_db.py` 一處。`src/dashboard.py` 的 `_BENCH`
  直接指向同一個 dict，前端即時重算與後端評分不可能再漂移。
- 查無晶片時 `get_benchmark()` 回傳 5000（極低分），物件仍會顯示但排在末端；
  這與「抽不到晶片就丟棄」是不同層級的行為，不可混淆。
