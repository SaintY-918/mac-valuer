# 資料模型（Data Models）

三個型別構成整條 pipeline 的骨架：爬蟲產出 `RawListing`，解析產出
`MacBookSpec`，計分讀 `MacBookSpec` 與 `ScoringWeights`。

---

## 1. `RawListing`（`src/scrapers/base.py`）

爬蟲與 pipeline 之間的唯一契約。**所有爬蟲的輸出都必須是這個型別**，
平台差異止於 scraper 內部。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `url` | str | 唯一識別。蝦皮詳情頁模式下為 Synthetic URL（`商品URL?m=modelid`） |
| `title` | str | 賣家原始標題，不做正規化 |
| `body_content` | str | 內文，上限 800 字元（L3） |
| `source` | str | `ptt` / `shopee` / `carousell` |
| `status` | str | `available` / `sold` |

- **`body_content` 不是裝飾用的。** `find_defects()` 主要讀它——PTT 賣家慣常
  把機況寫在內文而不是標題。任何讓 `body_content` 變薄的取得方式（例如蝦皮
  聯盟 API 不提供商品描述）都會連帶削弱瑕疵偵測，屬於必須明示的取捨。
- **失敗必須拋例外，不得回傳空 list。** 空 list 的語意是「本次無符合物件」，
  與「爬蟲壞掉」是不同事件，混淆會使 heartbeat 無法反映故障。

---

## 2. `MacBookSpec`（`src/models/mac_spec.py`）

解析結果。**除了兩個推論旗標，每個欄位都是 `Optional`，而且這是刻意的。**

名稱是歷史遺留：它涵蓋所有 Mac（MacBook、Mac mini、Mac Studio），不只筆電。
改名會動到十幾個檔案與所有 spec，換不到任何行為，所以不改。

| 欄位 | 型別 | 備註 |
|---|---|---|
| `chip` | str? | 例：`M1`、`M2 Pro`、`A18 Pro`、`M3 Ultra` |
| `ram_gb` / `ssd_gb` | int? | 單位 GB |
| `screen_size` | float? | 英吋。**桌機一律 `None`**，解析清理階段強制清空 |
| `release_year` | int? | 缺值時計分回退 2020 |
| `series` | `ModelSeries`? | |
| `price` | float? | |
| `location` | str? | |
| `battery_health` | int? | 百分比。**桌機一律 `None`** |
| `warranty_status` / `condition` | str? | |
| `is_year_inferred` | bool | 年份為推得而非讀得 |
| `is_spec_inferred` | bool | 規格為推得而非讀得 |

### 為什麼全部可為空

**寧可缺值，不可猜測。** 猜錯的值會一路傳到分數與警報，而且從結果上看不出
它是猜的；缺值則會停在原地並被下游明確處理。

但缺值不是沒有代價，必須知道代價在哪：**`release_year` 缺失時計分回退 2020**，
把一台當代機器當成六年前的機器，分數腰斬——同一款商品曾同時出現 432 與 204
兩種分數。因此年份缺失被列為 `needs_fix` 的條件之一。

### 推論旗標

`is_year_inferred` / `is_spec_inferred` 存在的理由是**讓「讀到的」與「推出的」
在資料層可分辨**。凡是推得的值，呈現時不得與實測值混為一談。

### `ModelSeries`

`Air` / `Pro 13` / `Pro 14/16` / `Neo` / `Mac mini` / `Mac Studio` 六個值。

- **新機型必須先加進這個 enum**，否則 pydantic 會拒絕整筆解析結果，
  該機型會整批消失。MacBook Neo（2026-03）就是這樣被丟掉的：解析器
  「無法描述」一台 Neo。
- **此類別不得帶權重屬性。** 它曾經帶過第三套與計分不一致的乘數，且無人使用。
  計分乘數只存在於 `ScoringWeights`。
- iMac 與 Mac Pro 刻意不在其中（延後，見 decisions #38 的後續條目（待補））。

### `device_class`：由 `series` 推導，不另存

```
laptop  ── Air / Pro 13 / Pro 14/16 / Neo
desktop ── Mac mini / Mac Studio
```

- Mac mini 與 Mac Studio 是**同一類東西**（沒有螢幕、沒有電池的主機），彼此的差異
  像 Air 與 Pro 一樣落在 `series` 這一層，不是各自一類。
- `device_class(series)` 是唯一的判定入口，`DESKTOP_SERIES` 是唯一的清單。
  **不得把 `device_class` 存進 `parsed_json`**——它是 `series` 的函數，存第二份就是
  第二個會漂移的來源。
- `None`、`NaN`、空字串一律視為 `laptop`：桌機出現之前寫入的每一列都是筆電。
- 用到它的地方：計分的形態加成（`form_factor_key`）、解析迴圈的必要欄位
  （桌機不要求 `screen_size`）、Dashboard 的模式切換、API 的 `device_class` 參數、
  Discord 的警報門檻。

### `VALID_RAM_GB` / `VALID_SSD_GB`

Apple 實際出貨的配置，放在模型層而非解析器裡，因為**解析器與 Dashboard 的
篩選選單都需要它**。兩份清單曾經漂移——篩選器停在 64 GB / 2 TB，
而解析器早已接受 128 GB / 8 TB，使得篩選會濾掉合法資料。
**任何需要「有效配置」的地方都必須由此推導，不得自行列舉。**

---

## 3. `ScoringWeights`（`src/calculator/score_engine.py`）

計分乘數的**唯一**存放處。Dashboard 的滑桿直接寫進這些欄位。

| 欄位 | 預設 | 套用條件 |
|---|---|---|
| `ram_multiplier` | 1.25 | `ram_gb >= 16` |
| `ssd_multiplier` | 1.10 | `ssd_gb >= 1024` |
| `form_air13` | 1.00 | |
| `form_air15` | 1.08 | |
| `form_pro13` | 1.00 | |
| `form_pro14` | 1.18 | |
| `form_pro16` | 1.22 | |
| `form_mini` | 1.00 | `series == Mac mini` |
| `form_studio` | 1.05 | `series == Mac Studio` |

- **形態依螢幕尺寸切分，不是只看 `series`。** 15 吋 Air 與 16 吋 Pro 相對於
  小尺寸手足有溢價，合併會失去這個資訊。`form_factor_key(series, screen_size)`
  是這條規則唯一存在的地方。
- **桌機先於尺寸判定。** `form_factor_key` 先問 `device_class`，桌機直接回
  `mini`／`studio`；否則缺尺寸時的 13.3 吋預設會把每台 Mac mini 歸進 `pro13`。
- **桌機乘數不是用來讓桌機分數與筆電對齊的。** 沒有螢幕與電池的主機每千元買到的
  效能天生較高，分數會比筆電高一截，這是事實不是誤差。兩類各自對自己的中位數比較
  （見 `score-engine/spec.md`）。
- `FORM_KEYS_BY_CLASS` 與 `FORM_LABELS` 由此推導，Dashboard 一次只顯示一類的滑桿。
- **`DEFAULT_WEIGHTS` 是一個具名的共享實例。** 預設引數在定義時求值一次，
  所以 `weights=ScoringWeights()` 會讓所有呼叫端共用同一個可變物件——
  具名是為了讓這件事變成明示而非意外。
- **公式只有一份實作。** Dashboard 曾自帶一份公式與一份基準表，兩者漂移到
  125 筆中有 59 筆分數不同（最大差 55 分），7 筆落在 Discord 警報門檻兩側——
  頁面顯示「優秀」卻從未警報，或警報了卻顯示為普通。
  持有 `MacBookSpec` 的呼叫端用 `get_vfm_score`；持有原始列的用
  `vfm_from_mapping`。**不得出現第三條路徑。**

---

## 跨模型的通則

1. **常數只能有一個來源。** 本專案已修過三次同型缺陷：晶片基準分被手抄進
   說明文字、篩選上限被手抄進 Dashboard、晶片世代清單被複製進爬蟲過濾器。
   需要一份清單時，從定義它的地方推導。
2. **寧可缺值，不可猜測**（decisions #9）。若必須以模型名反推規格
   （例如 MacBook Neo → A18 Pro），該對照必須：明示只在完全抽不到時套用、
   明示它是有到期日的產品事實、並記下失效時該改哪裡。
3. **規則改變時，已存的資料不會自己跟上**（decisions #23）。
   改動任何影響既有列的判定後，以 `src/scripts/revalidate_chips.py`（晶片）或
   `src/scripts/revalidate_series.py`（機型／桌機）重新檢驗。
