# mac-valuer

二手 Mac 好價雷達。從三個平台自動蒐集販售資訊，用 LLM 解析規格，
換算成可跨機型比較的性價比分數，找出市場上真正划算的機器。

**線上版**：[mac-valuer.streamlit.app](https://mac-valuer.streamlit.app)

---

## 這個專案在解決什麼

買二手 MacBook 的困難不在找不到物件，而在**無法比較**：

- 一台 2020 年的 M1 賣 15,000，一台 2025 年的 M4 賣 30,000，哪個划算？
- 同樣是 M3，16G/512G 和 8G/256G 的合理價差是多少？
- 賣家的標題寫法各不相同，規格散落在標題、內文、甚至網址裡

系統把每筆物件換算成 **VFM（Value For Money）分數**——每花一千元買到多少效能——
讓不同世代、不同規格的機器可以並排比較。

---

## 架構

```
PTT MacShop (Atom)     蝦皮 (瀏覽器)      旋轉拍賣 (JSON-LD)
        │                    │                    │
        └────────────────────┼────────────────────┘
                             ▼
                  BaseScraper（Strategy Pattern）
                             ▼
              Gemini LLM 解析 ＋ Regex 抽取（規則優先）
                             ▼
                    Neon PostgreSQL
                             ▼
                      VFM 評分引擎
                    ┌────────┴────────┐
                    ▼                 ▼
          Streamlit Dashboard    Discord 撿漏推播
```

**執行位置**：PTT 與旋轉拍賣跑在 GitHub Actions（純 HTTP，無反爬牆）；
蝦皮需住宅 IP，跑在本機排程。原因見下。

---

## 幾個值得一提的工程決策

### 蝦皮擋的是 IP，不是憑證 —— 這是實測，不是推測

雲端排程的蝦皮資料長期為 0。原本歸納出四個原因，但其中三個都能在 CI 修好，
所以第四個「IP 被擋」從來沒有被驗證的機會。

我補齊了 session 還原與瀏覽器安裝，讓 runner 帶著 **22 個有效 cookie** 去存取，結果仍被導向：

```
shopee.tw/verify/captcha?...&scene=crawler_item
```

`scene=crawler_item` 是蝦皮自己的分類標記。**憑證有效卻仍被攔，代表判斷依據是執行環境。**
換任何免費雲端主機都是同樣結果——稀缺的不是運算資源，是非機房 IP。

### 與其硬碰反爬，不如找願意讓你抓的平台

蝦皮需要住宅 IP、登入 session，且會週期性跳驗證碼，無法完全無人值守。
旋轉拍賣的 `robots.txt` 明確寫出哪些能爬：

```
Disallow: /search/          # 不碰
Disallow: /*?               # 不用任何帶查詢字串的網址
Sitemap:  ...               # 主要入口
```

照著它的規則走，從官方 sitemap 取商品、解析 schema.org JSON-LD——
**純 HTTP、不需瀏覽器、可在 CI 執行**，穩定度與 PTT 同級。

### 評分公式獎勵低價，而壞掉的機器正因為壞掉才便宜

實測分數最高的兩筆分別是「瑕疵機」與「外接機」（螢幕已壞），第一名還掛著「最划算」徽章。

處理方式是**標註而不扣分**：折價多少沒有客觀答案，任何懲罰係數都是憑空訂的；
而便宜的瑕疵機對能自行維修的人仍是合理選擇。提供資訊、由人判斷。

只標**功能性**瑕疵——外觀使用痕跡是二手常態，全部標註會讓讀者學會忽略徽章。
偵測需處理否定詞：真實資料裡有「外觀**無**傷**無**碰撞」這種含關鍵字但語意相反的寫法。

其餘決策記錄於 [`docs/decisions.md`](docs/decisions.md)，包含：規格抽取為何寧可缺值也不猜測、
「本次沒看到」為何不等於下架、行情價顯示為何延後（附各世代的實測偏差數據），
以及前後端評分公式如何從兩份實作收斂為一份。

---

## 技術棧

| 層 | 選擇 |
|---|---|
| 爬蟲 | `feedparser`（PTT）、`camoufox`（蝦皮反指紋）、`requests`（旋轉拍賣） |
| 解析 | Gemini 3.5 Flash Lite ＋ 自訂 regex；規則優先、LLM 補漏 |
| 資料庫 | SQLAlchemy → Neon PostgreSQL |
| 前端 | Streamlit，套用自有設計系統 |
| API | FastAPI |
| 排程 | GitHub Actions ＋ Windows 工作排程器 |

---

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/setup.md`](docs/setup.md) | 安裝、環境變數、雲端部署 |
| [`docs/operations.md`](docs/operations.md) | 日常指令、蝦皮申請、疑難排解 |
| [`docs/decisions.md`](docs/decisions.md) | 決策紀錄，含**延後與否決**的方案 |
| [`CHANGELOG.md`](CHANGELOG.md) | 變更時間軸 |
| [`.spec/specs/`](.spec/specs/) | 各模組實作規格 |
| [`CLAUDE.md`](CLAUDE.md) | AI 協作規範 |

---

## 專案結構

```
src/
├── main.py                 主流程
├── scrapers/
│   ├── base.py             BaseScraper 介面、RawListing
│   ├── ptt.py              PTT MacShop
│   ├── shopee.py           蝦皮（瀏覽器；依金鑰自動切換 API）
│   ├── shopee_api.py       蝦皮聯盟行銷 Open API
│   └── carousell.py        旋轉拍賣
├── parser/
│   ├── llm_parser.py       Gemini 解析、規格抽取、節流
│   ├── text_extractor.py   PTT 結構化區塊抽取
│   └── condition_flags.py  瑕疵偵測
├── calculator/score_engine.py   VFM 公式
├── utils/benchmark_db.py        晶片基準分（Geekbench 6 多核）
├── database/db_manager.py       SQLAlchemy 操作
├── notifier/discord_notify.py   撿漏推播、每日 heartbeat
├── scripts/                     診斷與維運工具
└── dashboard.py                 Streamlit 前端
```

---

## 品質關卡

每次 push 與 PR 自動跑三道，任一失敗即擋下（[`.github/workflows/tests.yml`](.github/workflows/tests.yml)）：

```bash
ruff check .                          # linter，規則經篩選，見 ruff.toml
python scripts/check_docs.py          # 文件與程式碼是否還說同一件事
pytest tests/ --ignore=tests/e2e      # 111 項純邏輯，約 2 秒
pytest tests/e2e                      # 19 項瀏覽器實測，約 20 秒
```

**測試套件刻意不吃 API 金鑰、也不連資料庫。** 這條限制不是為了方便，是它逼著
純運算（規格抽取、晶片判定、VFM、瑕疵偵測、下架判定）與 I/O 分離——能離線測的
邏輯才是真的獨立。

文件檢查驗四件事：env 變數兩邊對得上、Markdown 連結指得到檔案、CLAUDE.md 宣稱
已寫的 spec 真的存在、以及 spec 裡引用的評分常數與 `score_engine.py` 一致。
最後一項最要緊——讀者是照那張表判斷分數合不合理的。

瀏覽器測試自己建一個固定的 SQLite 測試資料庫、用真正的進入點把 Streamlit 跑起來，
再用 Playwright 驗**畫面上的東西**：卡片分數與 `score_engine` 算出來的一致、
瑕疵徽章出現在該出現的地方、320／390／1440px 都不橫向捲動、零 JS 錯誤。
同樣不需要金鑰。

**為什麼要驗版面**：其餘所有測試都在測函式。分數算對了卻 render 進一個
在手機上撐破畫面的元素，對使用者而言一樣是失敗，而專案裡沒有任何其他東西
會發現這件事。

---

## 已知問題

- **蝦皮無法完全自動化**：需住宅 IP，且會週期性要求手動通過驗證碼。
- **行情價顯示尚未實作**：模型對不同世代非中性，需各世代累積 30 筆以上才有統計意義。
  → [`docs/decisions.md` #5](docs/decisions.md)

---

## 授權與注意事項

- `.env`、`shopee_state.json`、`*.db` 已列入 `.gitignore`，請勿提交真實金鑰。
- 爬蟲遵循各平台的 `robots.txt`；旋轉拍賣僅使用其 sitemap 與商品頁，不碰搜尋頁。
- 晶片基準分取自 Geekbench 6 多核心，來源標註於 `src/utils/benchmark_db.py`。
