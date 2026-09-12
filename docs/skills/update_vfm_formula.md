# Skill：修改 VFM 動態權重與前端顯示

> 適用情境：調整評分公式的晶片基準分、折舊率、形態加成、RAM/SSD 閾值，或修改 Dashboard 顏色分級邏輯。
> 閱讀 spec 前置：`.spec/specs/score-engine/spec.md`、`.spec/specs/data-models/spec.md`

---

## 架構速覽

```
ScoringWeights (data-models)
    ↓ 傳入
adjusted_score() / get_vfm_score() / vfm_from_mapping()   ← src/calculator/score_engine.py
    ↑ 查表
CHIP_BENCHMARKS          ← src/utils/benchmark_db.py
    ↓ 結果送給
Dashboard / API          ← src/dashboard.py / api/main.py
    ↓ 顏色分級
同類百分位閾值 (p50/p75)  ← dashboard.py 與 api/main.py 各自計算，按 device_class 分開
```

`scripts/check_docs.py` 會比對 `score-engine/spec.md` 表格裡的數字與 `score_engine.py` 的常數，
改了其中一邊沒改另一邊，CI 會紅。

---

## 情境一：調整晶片基準分

**檔案：** `src/utils/benchmark_db.py`

直接修改 `CHIP_BENCHMARKS` 字典。新增晶片也在此處新增 key-value，並在檔頭註解標註來源網址：

```python
CHIP_BENCHMARKS: Dict[str, int] = {
    "M1": 8500,
    # ... 現有晶片 ...
    "M6": 21000,        # ← 新增，附 Geekbench 6 多核來源
}
```

**規則：**
- 全表只能用 **Geekbench 6 多核心**，混用跑分平台會讓分數不可比較
- 未知晶片 fallback 為 `5000`（在 `get_benchmark()` 中，禁止修改此預設值）
- 查詢是大小寫不敏感的，新增 key 用正確大小寫即可
- 修改後同步更新 `.spec/specs/score-engine/spec.md` 的基準分段落

---

## 情境二：修改 ScoringWeights 預設值

**檔案：** `src/calculator/score_engine.py`

`ScoringWeights` 是 Pydantic BaseModel，修改 `default` 值即可：

```python
class ScoringWeights(BaseModel):
    ram_multiplier: float = 1.25
    ssd_multiplier: float = 1.10
    form_air13:  float = 1.00
    form_air15:  float = 1.08
    form_pro13:  float = 1.00
    form_pro14:  float = 1.18
    form_pro16:  float = 1.22
    form_mini:   float = 1.00
    form_studio: float = 1.05
```

改完要同步改 `.spec/specs/score-engine/spec.md` 的「形態加成」那一列，
`check_docs.py` 會依序比對七個數字。

---

## 情境三：新增一個形態（例如新的桌機或新尺寸）

要改三個地方，缺一個就會有一條路徑拿到 1.0 的預設乘數而沒人發現：

1. **`ScoringWeights`** 加欄位 `form_<key>`。
2. **`form_factor_key()`** 讓對應的 `series`／`screen_size` 回傳 `<key>`。
   桌機在函式開頭依 `device_class()` 判定，筆電才看尺寸。
3. **`FORM_KEYS_BY_CLASS`** 把 `<key>` 放進所屬類別，**`FORM_LABELS`** 給滑桿標籤。
   Dashboard 的滑桿與預設值由這兩個表推導，不需要改 `dashboard.py`。

若是全新的 `series`，還要先加進 `src/models/mac_spec.py` 的 `ModelSeries`
（否則 pydantic 會拒絕整筆解析結果），桌機類的要一併加進 `DESKTOP_SERIES`。
筆電類另有 `FORM_INCHES`／`FAMILY_INCHES` 決定卡片顯示尺寸與篩選選項。

---

## 情境四：修改折舊率

**檔案：** `src/calculator/score_engine.py`

```python
DEPRECIATION_RATE = 0.10   # 目前每年折舊 10%，想改 8% → 改為 0.08
```

公式：`depreciation = (1 - DEPRECIATION_RATE) ** age`，`age = 今年 - release_year`。
spec 表格寫的是 `×0.9`，改了要同步。

---

## 情境五：修改 RAM/SSD 加成閾值

**檔案：** `src/calculator/score_engine.py`

```python
RAM_BONUS_THRESHOLD_GB = 16
SSD_BONUS_THRESHOLD_GB = 1024
```

Dashboard 的滑桿標籤與說明區都讀這兩個常數，不用另外改。spec 表格要同步。

---

## 情境六：修改 Dashboard VFM 顏色分級

**檔案：** `src/dashboard.py`

顏色閾值必須基於**同一 device_class 全部在售物件**的百分位數，不受當前篩選影響；
`_load_available(klass)` 就是為此存在。同類在售少於 `MIN_BAND_SAMPLE` 筆時不分級。

**禁止做法：** 用篩選後的 `deals` 計算閾值，或把筆電與桌機混在一起算——
桌機每千元買到的效能天生較高，混算會讓每台 Mac mini 都是「划算」。

新增分級（例如「極優」）時，`_tier()`、圖例 HTML、`api/main.py` 的
`_compute_thresholds()` 與 spec 都要一起改。

---

## 修改後的驗證清單

1. **單元測試與文件一致性：**
   ```bash
   .\venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/e2e
   .\venv\Scripts\python.exe scripts/check_docs.py
   ```

2. **手動驗算：** 用一筆已知資料（例如 M2 Air 16GB/512GB 售價 30000，2022 年）手算 VFM，
   比對 Dashboard「VFM 分數構成」區的逐項數字。

3. **API 驗證：** `POST /api/score/calculate` 傳入自訂 weights，確認回傳值反映修改。

---

## 常見錯誤

| 錯誤 | 正確做法 |
|------|---------|
| 改了 `CHIP_BENCHMARKS` 或權重但 spec 沒同步 | `check_docs.py` 會擋，改完跑一次 |
| 用篩選後的資料或混合兩類算閾值 | 同類、全部在售 |
| 新增 `form_*` 欄位卻沒加進 `FORM_KEYS_BY_CLASS` | 滑桿不會出現，權重永遠是預設值 |
| `spec.price` 為 0 時未守衛就做除法 | `get_vfm_score()` 開頭已有守衛，禁止移除 |
