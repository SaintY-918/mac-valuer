# Database

**目標**：以單一資料表保存所有來源的物件，並讓「這筆還在架上嗎」這個問題有可信的答案。

實作：`src/database/db_manager.py`。SQLAlchemy ORM，正式環境為 Neon（PostgreSQL 免費方案，
0.5 GB），本機與測試為 SQLite。連線字串一律來自 `DATABASE_URL`。

---

## 5.X.1 Schema

單一資料表 `deals`，主鍵是 `url`。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `url` | String, PK | 物件網址，跨來源唯一 |
| `source` | String, not null | `ptt` / `shopee` / `carousell` |
| `title` | Text | 原始標題 |
| `body_content` | Text | 原始內文 |
| `parsed_json` | Text | 解析後的規格，JSON 字串 |
| `status` | String | `available` / `sold` / `unavailable` |
| `first_seen` | DateTime | INSERT 時寫入，**永不覆寫** |
| `updated_at` | DateTime | 追蹤欄位真的改變時才更新 |
| `last_seen` | DateTime | **每次在平台上看到就更新** |
| `last_alerted_price` | Integer | 上次發出警報時的價格，用於去重 |

**時間一律存 UTC。** `datetime.now(timezone.utc)`，欄位為 `TIMESTAMP WITHOUT TIME ZONE`，
Postgres 會捨棄位移只留下數值。要顯示台灣時間的地方自己加 8 小時（Dashboard 就是這樣做）。
不要在寫入端先轉成本地時間，否則伺服器換時區資料就毀了。

新欄位透過 `_migrate_db()` 加上——它比對 `information_schema`（SQLite 則是 `PRAGMA`），
缺什麼就 `ALTER TABLE`。**不要**用 `Base.metadata.create_all()` 當成 migration，
它只建不改。

---

## 5.X.2 三個時間欄位的分工

這是本模組最容易寫錯的地方，而且錯了不會有任何東西報錯。

- `first_seen`：**這筆資料第一次進資料庫**。用於「24 小時內新增」統計。永不覆寫。
- `updated_at`：**解析結果或狀態改變**。內容變動的稽核軌跡。
- `last_seen`：**我們最後一次在平台上看到這個物件**。`sweep_stale()` 只讀這一欄。

### `last_seen` 只能由「真的看到」來更新

`save_deal()` 代表一次目擊，它會更新 `last_seen`。
`update_parsed()` 只改 `parsed_json`，**不碰 `last_seen`**。

分成兩個方法不是為了整齊，是因為混用會造成靜默的資料腐爛：

> Step 2 的解析迴圈只讀資料庫裡已存的 `title` 與 `body_content`，**從不重新抓頁面**。
> 它曾經用 `save_deal()` 寫回結果，於是每一筆解析不完整的資料每晚都被蓋成「剛看到」。
> `sweep_stale()` 因此永遠不會讓它們過期——**186 筆中有 121 筆處於這個狀態**，
> 其中最糟的是那些「文字裡本來就沒有那個欄位」的資料：它們永遠解析不完整，
> 也就永遠不死，會在網站上以「在售中」的樣子留到天荒地老。

規則：**沒有真的去平台上看過，就不准動 `last_seen`。**

---

## 5.X.3 下架判定：以年齡為準，不以集合為準

`sweep_stale(source, max_age_days=14)` 把**超過 N 天沒被看到**的 `available` 資料標成
`unavailable`。

**不可以**改成「這次沒抓到的就標為下架」。每個爬蟲抓的都是一個**時間窗**而不是完整庫存：
PTT 讀最近的 Atom feed，蝦皮讀最新約 180 筆搜尋結果。「這次沒看到」通常只代表它滾出了
視窗。曾經的集合式 sweep 在單一次執行中把 46 筆still-live 的蝦皮物件標成下架。

真正的下架判定留在該留的地方：PTT 的標題關鍵字、蝦皮的庫存與灰底判定——那些是**去看物件本身**。

其他規則：

- 只掃**這次有跑而且沒失敗**的來源。來源整個失敗時什麼都沒刷新，這時 sweep 會把整個來源標死。
- 不動已經是 `sold` 或 `unavailable` 的資料。
- 一次只處理一個 `source`。

---

## 5.X.4 讀取路徑必須回傳呼叫端會用到的欄位

`get_all_deals()`、`get_filtered_deals()`、`get_all_parsed_deals()` 都是把 `parsed_json`
展開，再外加資料表本身的欄位。**外加清單漏欄位是這個模組重複犯過三次的錯**：

1. `source` 沒有從 `get_filtered_deals()` 回傳 → Dashboard 顯示不出來源
2. `body_content` 沒有從任何路徑回傳 → 只寫在內文的瑕疵對整個系統不存在（見 `api/spec.md` 4.X.5）
3. `source` 沒有從 `get_all_deals()` 回傳 → 解析迴圈無法依來源限縮範圍

這類錯誤不會拋例外，只會讓某個功能靜靜地失效。**新增欄位時，同時檢查三個讀取路徑。**

`body_content` 刻意**不**從讀取路徑回傳：Dashboard 每動一次滑桿就重查，
把所有內文從 Neon 拉出來只為了算一個布林值不划算。需要內文的判定在 pipeline 內完成
並存進 `parsed_json`。

---

## 5.X.5 寫入行為

- **四元組去重**：`(chip, ram_gb, ssd_gb, price)` 沒變就跳過重欄位寫入，但 `last_seen`
  仍然更新——它是目擊紀錄，與內容有沒有變無關。
- `save_deal()` 的例外一律記錄後吞掉。單筆寫入失敗不得中斷整批。
- `update_parsed()` 與 `update_last_alerted_price()` 對不存在的 url 回傳 `False` 而非拋錯：
  資料可能在兩次讀取之間被清掉。

---

## 5.X.6 額度

Neon 免費方案 0.5 GB。目前約 200 筆、每筆含內文約 2–4 KB，離上限還很遠，
但 `body_content` 是最大的欄位——真要省空間時先從它下手（例如只保留前 N 字元，
`carousell.py` 的 `L3_BODY_MAX_CHARS` 已經這樣做）。
