# Skill：修改 VFM 動態權重與前端顯示

> 適用情境：調整評分公式的晶片基準分、折舊率、形態加成、RAM/SSD 閾值，或修改 Dashboard 顏色分級邏輯。
> 閱讀 spec 前置：`openspec/specs/score-engine/spec.md`、`openspec/specs/data-models/spec.md`

---

## 架構速覽

```
ScoringWeights (data-models)
    ↓ 傳入
get_vfm_score()          ← src/calculator/score_engine.py
    ↑ 查表
CHIP_BENCHMARKS          ← src/utils/benchmark_db.py
    ↓ 結果送給
Dashboard / API          ← src/dashboard.py / api/main.py
    ↓ 顏色分級
全庫百分位閾值 (p50/p75)  ← dashboard.py 中計算
```

---

## 情境一：調整晶片基準分

**檔案：** `src/utils/benchmark_db.py`

直接修改 `CHIP_BENCHMARKS` 字典。新增晶片也在此處新增 key-value：

```python
CHIP_BENCHMARKS: Dict[str, int] = {
    "M1": 8500,
    "M1 Pro": 12000,
    # ... 現有晶片 ...
    "M5": 18000,        # ← 新增 M5
    "M5 Pro": 22000,    # ← 新增 M5 Pro
}
```

**規則：**
- 未知晶片 fallback 為 `5000`（在 `get_benchmark()` 中，禁止修改此預設值）
- 查詢是大小寫不敏感的（`get_benchmark()` 內已處理），新增 key 用正確大小寫即可
- 修改後記得同步更新 `openspec/specs/score-engine/spec.md` 的 benchmark table

---

## 情境二：修改 ScoringWeights 預設值

**檔案：** `src/calculator/score_engine.py`

`ScoringWeights` 是 Pydantic BaseModel，修改 `default` 值即可：

```python
class ScoringWeights(BaseModel):
    ram_multiplier: float = 1.25    # 想調高 RAM 加成 → 改為 1.30
    ssd_multiplier: float = 1.10    # 想調高 SSD 加成 → 改為 1.15
    form_air13:  float = 1.00
    form_air15:  float = 1.08
    form_pro13:  float = 1.00
    form_pro14:  float = 1.18       # 想提高 Pro 14" 加成 → 改為 1.22
    form_pro16:  float = 1.22
```

**注意：** 目前程式碼使用舊版欄位名稱（`model_weight_air`, `model_weight_pro13`, `model_weight_pro14_16`）。spec 要求遷移至 `form_*` 命名。如果你看到舊版欄位，請同步重構 `_model_weight()` 函數：

```python
# 舊版（需要遷移）
def _model_weight(spec: MacBookSpec, weights: ScoringWeights) -> float:
    series = str(spec.series or "").lower()
    if "14" in series or "16" in series:
        return weights.model_weight_pro14_16
    ...

# 新版（依 spec）
def _form_factor_weight(spec: MacBookSpec, weights: ScoringWeights) -> float:
    series = str(spec.series or "").lower()
    screen = spec.screen_size or 0
    if "pro" in series:
        if screen >= 15:
            return weights.form_pro16
        if screen >= 13.5:
            return weights.form_pro14
        return weights.form_pro13
    # Air
    if screen >= 14:
        return weights.form_air15
    return weights.form_air13
```

---

## 情境三：修改折舊率

**檔案：** `src/calculator/score_engine.py`

```python
DEPRECIATION_RATE = 0.10   # 目前每年折舊 10%，想改 8% → 改為 0.08
```

公式：`depreciation = (1 - DEPRECIATION_RATE) ** age`，`age = 今年 - release_year`

---

## 情境四：修改 RAM/SSD 加成閾值

**檔案：** `src/calculator/score_engine.py`

目前的邏輯在 `calculate_adjusted_score()` 中：

```python
ram_mult = weights.ram_multiplier if (spec.ram_gb or 0) >= 16 else 1.0
ssd_mult = weights.ssd_multiplier if (spec.ssd_gb or 0) >= 1024 else 1.0
```

若要改閾值（例如 RAM 從 16GB 改為 24GB）：
```python
ram_mult = weights.ram_multiplier if (spec.ram_gb or 0) >= 24 else 1.0
```

**同步更新：** 修改閾值後，必須更新 `openspec/specs/score-engine/spec.md` 中對應的 Scenario。

---

## 情境五：修改 Dashboard VFM 顏色分級

**檔案：** `src/dashboard.py`

顏色閾值必須基於**全庫資料的百分位數**，不受當前篩選影響。找到計算閾值的地方：

```python
# 正確做法：用全庫 vfm_score 計算 p50/p75
all_scores = [d["vfm_score"] for d in all_deals if d.get("vfm_score")]
p75 = np.percentile(all_scores, 75)
p50 = np.percentile(all_scores, 50)

def color_vfm(score: float) -> str:
    if score >= p75:
        return "🟢"
    if score >= p50:
        return "🟡"
    return "🔴"
```

**禁止做法：** 用 `filtered_deals` 計算閾值，會導致篩選時顏色分布失真。

修改顏色閾值分級（例如加入「極優」藍色分級）時，需要同步更新 `openspec/specs/score-engine/spec.md` 的 VFM color thresholds 表格。

---

## 修改後的驗證清單

1. **執行單元測試：**
   ```bash
   python test_engine.py
   ```

2. **手動驗算：** 用一筆已知資料（例如 M2 Air 16GB/512GB 售價 30000，2022 年）手算 VFM，比對程式輸出是否一致。

3. **spec 同步：** 確認 `openspec/specs/score-engine/spec.md` 的 Scenario 與新邏輯吻合。

4. **API 驗證：** 啟動 FastAPI 後，`POST /api/score/calculate` 傳入自訂 weights，確認回傳值反映修改。

---

## 常見錯誤

| 錯誤 | 正確做法 |
|------|---------|
| 直接改 `CHIP_BENCHMARKS` 但 spec 沒同步更新 | 改完必須同步更新 score-engine/spec.md |
| 用篩選後的資料計算顏色閾值百分位 | 必須用全庫所有資料計算 p50/p75 |
| 新增晶片 key 用小寫（`"m5"`） | `get_benchmark()` 內部會做 lowercase 比對，但 key 本身請保持正確大小寫 |
| `spec.price` 為 0 時未守衛就做除法 | `get_vfm_score()` 開頭已有 `if not spec.price or spec.price <= 0: return 0.0`，禁止移除這行 |
