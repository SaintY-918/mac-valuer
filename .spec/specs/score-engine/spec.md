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
- 現行實作以 `\bM(\d{1,2})\s*(PRO|MAX|ULTRA)?\b` 比對，新世代只需補基準分，不必改抽取邏輯。
- 同一標題出現多個晶片時取**最高階**（世代優先，同世代則 Ultra > Max > Pro > 無）。
- **含糊標題取低不取高**：賣家常堆關鍵字寫出不存在的「M1 Pro Max」。
  僅緊鄰世代的變體算數，因此判為 `M1 Pro`。低估 VFM 只是少一次機會，
  高估則會發出假的撿漏警報，後者代價大得多。

#### 6.X.3 基準分維護

- `CHIP_BENCHMARKS` 為專案內部的相對尺度（近似 Geekbench 6 多核），不是精確測值。
- **M5 系列（`M5` / `M5 Pro` / `M5 Max`）目前為外推值**，依世代間約 +17% 的趨勢推得，
  已在程式碼中明確標註。取得實測數據後應替換。
- 新增世代時必須**同時**更新兩處，否則 Dashboard 與後端評分不一致：
  1. `src/utils/benchmark_db.py` — 後端評分
  2. `src/dashboard.py` 的 `_BENCH` — 前端即時重算
- 查無晶片時 `get_benchmark()` 回傳 5000（極低分），物件仍會顯示但排在末端；
  這與「抽不到晶片就丟棄」是不同層級的行為，不可混淆。
