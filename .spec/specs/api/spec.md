### 4.X API 與 Dashboard

**目標**：對外提供估價資料查詢，並以 Streamlit Dashboard 呈現。

---

#### 4.X.1 Dashboard 與 API 的關係

- **Dashboard 不經過 FastAPI**。`src/dashboard.py` 直接呼叫 `DBManager.get_filtered_deals()`，因為 Streamlit Community Cloud 與 API 各自獨立部署，中間再架一層 HTTP 只會多一個故障點。
- FastAPI（`api/main.py`）是**給外部程式用的**獨立入口，與 Dashboard 平行，兩者共用 `DBManager` 與 `score_engine`。
- 兩邊的 VFM 計算必須產生相同結果。Dashboard 端為 `_recalc_vfm()`，API 端為 `_attach_vfm()`；**修改 VFM 公式時兩處都要改**，否則同一筆資料會有兩個分數。

#### 4.X.2 FastAPI Endpoints

| Method | Path | 說明 |
|---|---|---|
| GET | `/api/deals` | 條件查詢物件清單，附帶即時計算的 VFM 分數 |
| POST | `/api/score/calculate` | 單筆規格試算 VFM |
| GET | `/api/health` | 健康檢查 |

#### 4.X.3 資料存取契約

- `DBManager.get_filtered_deals()` 回傳的 dict 由 `parsed_json` 展開，**外加**下列來自資料表本身的欄位：`original_title`、`url`、`status`、`source`。
- 新增任何需要在 Dashboard 顯示的資料表欄位時，必須同步加入上述展開清單。`source` 曾經只用於 SQL 篩選而未回傳，導致 Dashboard 拿不到來源值。
- `status` 篩選在 SQL 層；`ssd_gb` 等其餘條件在 Python 層。

#### 4.X.4 Dashboard 呈現規範

- **物件清單以卡片格線呈現，不使用 `st.dataframe`。** 13 欄的表格在 700px 以下不可讀，且 `st.dataframe` 幾乎不開放樣式控制。
- **欄數採明確斷點，不使用 `auto-fill` + `minmax`**：側邊欄佔用的寬度會隨視窗變動，曾使「桌機 3 欄且平板 2 欄」只在 236–238px 這個 2px 的 `minmax` 窗口內成立，Streamlit 內距一改就會靜默翻掉版面。

  | 視窗寬度 | 欄數 |
  |---|---|
  | < 700px | 1 |
  | ≥ 700px | 2 |
  | ≥ 1200px | 3 |
  | ≥ 1800px | 4 |

- **所有動態內容必須經過 `html.escape()`**。標題與地區來自 PTT 與蝦皮的使用者輸入，未跳脫等同於 XSS。
- **視覺層級**：VFM 分數為主體（最大字級、依級距上色），售價次之，其餘為輔助資訊。卡片左側色條在讀到數字前就傳達級距。
- **行動版約束**：頁面不得產生水平捲動；第一張卡片必須落在首屏內（320px 寬亦同）。標題、統計列、圖例在 ≤640px 時需縮排，否則頁首會吃掉整個首屏。
- **等高卡片**：標題固定佔兩行（`min-height` + `line-clamp`），地區單行截斷。PTT 賣家會把整句話寫進地區欄位，不截斷會讓單一物件撐高整列。
