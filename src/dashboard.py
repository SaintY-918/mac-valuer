import datetime
import os
from datetime import timedelta
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── DATABASE_URL injection ─────────────────────────────────────────────────────
# Streamlit Community Cloud provides secrets via st.secrets; fall back to env var
# for local development. Must happen before DBManager is first imported/used.
try:
    _db_url = st.secrets.get("DATABASE_URL")
except st.errors.StreamlitSecretNotFoundError:
    _db_url = None
if not _db_url:
    _db_url = os.getenv("DATABASE_URL")
if _db_url:
    os.environ["DATABASE_URL"] = _db_url

from src.database.db_manager import DBManager  # noqa: E402 (after env injection)

_BENCH = {
    "M1": 8500, "M1 Pro": 12000, "M1 Max": 12500,
    "M2": 10000, "M2 Pro": 14000, "M2 Max": 14500,
    "M3": 11500, "M3 Pro": 15500, "M3 Max": 21000,
    "M4": 14500, "M4 Pro": 22000, "M4 Max": 26000,
}

_DEFAULT_FORM_W = {
    "air13":  1.00,
    "air15":  1.08,
    "pro13":  1.00,
    "pro14":  1.18,
    "pro16":  1.22,
}


def _form_key(row: dict) -> str:
    s  = str(row.get("series") or "").lower()
    sc = float(row.get("screen_size") or 13.3)
    if "air" in s:
        return "air15" if sc >= 15.0 else "air13"
    if sc >= 15.0:
        return "pro16"
    if sc >= 14.0:
        return "pro14"
    return "pro13"


def _nan_safe(val, default):
    import math
    if val is None:
        return default
    try:
        if math.isnan(float(val)):
            return default
    except (TypeError, ValueError):
        pass
    return val or default


def _recalc_vfm(row: dict, w: dict) -> float:
    price = float(_nan_safe(row.get("price"), 0))
    if price <= 0:
        return 0.0
    chip  = str(row.get("chip") or "")
    base  = _BENCH.get(chip, 5000)
    year  = int(_nan_safe(row.get("release_year"), 2020))
    age   = max(0, datetime.date.today().year - year)
    depr  = 0.9 ** age
    ram_m  = w["ram"] if _nan_safe(row.get("ram_gb"), 0) >= 16   else 1.0
    ssd_m  = w["ssd"] if _nan_safe(row.get("ssd_gb"), 0) >= 1024 else 1.0
    form_m = w.get(_form_key(row), 1.0)
    return round(base * depr * ram_m * ssd_m * form_m / price * 1000, 2)


@st.cache_resource
def _get_db() -> DBManager:
    return DBManager()


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="mac-valuer | 二手 MacBook 估價", page_icon=":material/laptop:", layout="wide")

st.markdown("""
<style>
/* ── Design tokens ──────────────────────────────────────────────────────────
   The dashboard is an instrument, not a shop: the VFM readout is the subject
   of every card and everything else is subordinate scale markings. Numerics
   are monospaced so figures line up down a column and stay comparable. */
:root {
    --surface:      #171725;
    --surface-hi:   #1e1e30;
    --border:       #2a2a3e;
    --border-hi:    #3d3d57;
    --text:         #e8e8f0;
    --text-dim:     #94a3b8;
    --text-faint:   #64748b;
    --vfm-good:     #4ade80;
    --vfm-mid:      #fbbf24;
    --vfm-poor:     #f87171;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo, Consolas, monospace;
}

[data-testid="stMetric"] { background: var(--surface); border-radius:10px; padding:12px 16px; }
[data-testid="stSidebarContent"] { background:#16162a; }

div[data-testid="stHorizontalBlock"] .page-btn button {
    border-radius: 6px;
    font-weight: 600;
}

/* ── Deal card grid ─────────────────────────────────────────────────────────
   Explicit column counts rather than auto-fill + minmax. The sidebar eats a
   variable slice of the viewport, which left only a 2px window of minmax values
   that produced 3 columns on desktop AND 2 on tablet — any change to Streamlit's
   padding would have silently flipped the layout. Breakpoints say what we mean.
   Measured grid widths with the sidebar open: 1440px viewport -> 980px grid,
   820px -> 488px, 390px -> 358px. */
.deal-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
    margin: 4px 0 18px;
}
@media (min-width: 700px)  { .deal-grid { grid-template-columns: repeat(2, 1fr); } }
@media (min-width: 1200px) { .deal-grid { grid-template-columns: repeat(3, 1fr); } }
@media (min-width: 1800px) { .deal-grid { grid-template-columns: repeat(4, 1fr); } }

.deal-card {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 16px 14px 19px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    text-decoration: none !important;
    color: var(--text) !important;
    overflow: hidden;
    transition: border-color .16s ease, transform .16s ease, background .16s ease;
}
/* Accent rail carries the VFM verdict before any number is read. */
.deal-card::before {
    content: "";
    position: absolute;
    inset: 0 auto 0 0;
    width: 3px;
    background: var(--tier);
}
.deal-card:hover {
    border-color: var(--border-hi);
    background: var(--surface-hi);
    transform: translateY(-2px);
}
/* Pointer-only: a phone has no hover, and the lift would fire on tap. */
@media (hover: none) {
    .deal-card:hover { transform: none; background: var(--surface); border-color: var(--border); }
}

/* Rank 1 on page 1 is the answer to the question the product asks, so it gets
   width instead of a badge — but only where there are columns to spare. */
@media (min-width: 900px) {
    .deal-card--top { grid-column: span 2; }
}

.deal-card__head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
}
.deal-card__vfm {
    font-family: var(--mono);
    font-size: 1.75rem;
    font-weight: 700;
    line-height: 1;
    letter-spacing: -0.02em;
    color: var(--tier);
}
.deal-card__vfm span {
    font-size: 0.62rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--text-faint);
    margin-left: 5px;
}
.deal-card__src {
    flex-shrink: 0;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    padding: 2px 7px;
    border-radius: 4px;
    border: 1px solid var(--border-hi);
    color: var(--text-dim);
}

.deal-card__title {
    font-size: 0.86rem;
    font-weight: 600;
    line-height: 1.4;
    color: var(--text);
    /* Always two lines tall, clamped: keeps the spec row at the same height in
       every card so the grid reads as a comparison table, not a ragged pile. */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
    min-height: 2.4em;
}

.deal-card__specs {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
}
.deal-card__specs b {
    font-family: var(--mono);
    font-size: 0.7rem;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
    background: #ffffff0a;
    color: var(--text-dim);
}
.deal-card__specs b.chip { color: var(--text); background: #ffffff14; }

.deal-card__foot {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-top: auto;
    padding-top: 9px;
    border-top: 1px solid var(--border);
}
.deal-card__price {
    font-family: var(--mono);
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text);
}
.deal-card__price s {
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--text-faint);
    margin-right: 3px;
    text-decoration: none;
}
.deal-card__meta {
    font-size: 0.72rem;
    color: var(--text-dim);
    text-align: right;
    /* PTT sellers write whole sentences into the location field; without this
       one listing's "面交地點:林口家樂福 / 雙北各捷運站…" sets the row height. */
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* ── Stat row ───────────────────────────────────────────────────────────────
   Colours are set explicitly: inheriting left the figures at the muted
   markdown colour, so the numbers read dimmer than their own labels. */
.stat-row { display: flex; flex-wrap: nowrap; gap: 10px; margin: 6px 0 10px; }
.stat {
    flex: 1 1 0;
    min-width: 0;
    padding: 9px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
}
.stat--wide { flex: 1.7 1 0; }
.stat span {
    display: block;
    font-size: 0.72rem;
    color: var(--text-dim);
    margin-bottom: 2px;
    white-space: nowrap;
}
.stat b {
    font-family: var(--mono);
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--text);
    white-space: nowrap;
}

.vfm-legend {
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    padding: 2px 0 8px; font-size: 0.82rem; color: var(--text-dim);
}
.vfm-legend i { font-style: normal; font-weight: 700; }

/* ── Mobile: the header used to eat the whole first screen ──────────────────
   At 390x900 the title, three stacked stat boxes and a wrapped legend pushed
   the first deal card past 1150px, so a phone user landed on a page with no
   deals visible at all. Shrink the chrome so cards start above the fold. */
@media (max-width: 640px) {
    h1 { font-size: 1.5rem !important; line-height: 1.25 !important; }
    .stat { padding: 7px 10px; border-radius: 8px; }
    .stat span { font-size: 0.62rem; }
    .stat b { font-size: 0.86rem; }
    .stat--wide b { font-size: 0.7rem; }
    .vfm-legend { gap: 7px; font-size: 0.72rem; }
    .deal-grid { gap: 10px; }
    .block-container { padding-top: 2.2rem !important; }
}

/* Keep pagination row horizontal on all screen sizes */
[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.5rem;
    align-items: center;
}
[data-testid="stColumn"] {
    min-width: 0 !important;
    flex-shrink: 1 !important;
}

/* Title: break only at spaces (after MacBook on mobile), never mid-CJK-word */
h1 { word-break: keep-all; overflow-wrap: normal; }

/* Hide Streamlit's auto-injected anchor icon on headings */
h1 a.anchor-link, h2 a.anchor-link, h3 a.anchor-link { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## :material/search: 篩選條件")

    if "min_price" not in st.session_state:
        st.session_state.update({
            "model_type": None, "chip_input": "", "ram_gb": None,
            "ssd_gb_filter": None, "screen_size": None,
            "min_price": 0, "max_price": 0, "show_sold": False,
            "source_filter": ["ptt", "shopee"],
            "ram_mult": 1.25, "ssd_mult": 1.1,
            "w_air13": 1.00, "w_air15": 1.08,
            "w_pro13": 1.00, "w_pro14": 1.18, "w_pro16": 1.22,
            "page_num": 1,
        })

    model_type = st.selectbox(
        "機型",
        [None, "Air", "Pro"],
        format_func=lambda x: "不限" if x is None else f"MacBook {x}",
        key="model_type",
    )
    chip_input = st.text_input("晶片型號（模糊）", placeholder="例如：M3", key="chip_input")
    ram_gb = st.selectbox(
        "RAM 大小",
        [None, 8, 16, 18, 24, 32, 36, 48, 64],
        format_func=lambda x: "不限" if x is None else f"{x} GB",
        key="ram_gb",
    )
    ssd_gb_filter = st.selectbox(
        "SSD 大小",
        [None, 256, 512, 1024, 2048],
        format_func=lambda x: "不限" if x is None else (f"{x} GB" if x < 1000 else f"{x // 1024} TB"),
        key="ssd_gb_filter",
    )
    screen_size_filter = st.selectbox(
        "螢幕尺寸",
        [None, 13, 14, 15, 16],
        format_func=lambda x: "不限" if x is None else f"{x} 吋",
        key="screen_size",
    )
    min_price = st.number_input("最低價格 (TWD)", min_value=0, step=1000, key="min_price")
    max_price = st.number_input("最高價格 (TWD)", min_value=0, step=1000, key="max_price")
    source_filter = st.multiselect(
        "賣場來源",
        options=["ptt", "shopee"],
        format_func=lambda x: "PTT MacShop" if x == "ptt" else "蝦皮",
        key="source_filter",
    )
    show_sold = st.checkbox("顯示已售出物件", key="show_sold")

    st.divider()

    def _reset_vfm_weights():
        st.session_state.update({
            "ram_mult": 1.25, "ssd_mult": 1.1,
            "w_air13": 1.00, "w_air15": 1.08,
            "w_pro13": 1.00, "w_pro14": 1.18, "w_pro16": 1.22,
        })

    with st.expander(":material/tune: VFM 評分設定"):
        st.caption("調整各項規格加權，分數即時重算")
        ram_mult = st.slider("RAM 加權（≥ 16 GB）", min_value=1.0, max_value=2.0, step=0.05, key="ram_mult")
        ssd_mult = st.slider("SSD 加權（≥ 1 TB）", min_value=1.0, max_value=2.0, step=0.05, key="ssd_mult")
        st.caption(":material/laptop: 機型 × 螢幕組合加權")
        w_air13 = st.slider('Air 13"',  min_value=0.5, max_value=2.0, step=0.05, key="w_air13")
        w_air15 = st.slider('Air 15"',  min_value=0.5, max_value=2.0, step=0.05, key="w_air15")
        w_pro13 = st.slider('Pro 13"',  min_value=0.5, max_value=2.0, step=0.05, key="w_pro13")
        w_pro14 = st.slider('Pro 14"',  min_value=0.5, max_value=2.0, step=0.05, key="w_pro14")
        w_pro16 = st.slider('Pro 16"',  min_value=0.5, max_value=2.0, step=0.05, key="w_pro16")
        st.button(":material/restart_alt: 重置評分設定", on_click=_reset_vfm_weights, use_container_width=True)

    st.divider()

    def _reset_all():
        st.session_state.update({
            "model_type": None, "chip_input": "", "ram_gb": None,
            "ssd_gb_filter": None, "screen_size": None,
            "min_price": 0, "max_price": 0, "show_sold": False,
            "source_filter": ["ptt", "shopee"],
            "ram_mult": 1.25, "ssd_mult": 1.1,
            "w_air13": 1.00, "w_air15": 1.08,
            "w_pro13": 1.00, "w_pro14": 1.18, "w_pro16": 1.22,
            "page_num": 1,
        })

    st.sidebar.button("重置", use_container_width=True, on_click=_reset_all)

    # ── Data freshness timestamp ───────────────────────────────────────────────
    try:
        _last_seen = _get_db().get_last_seen()
        if _last_seen:
            _tw = _last_seen + timedelta(hours=8)
            st.sidebar.caption(f"🔄 資料庫最後更新時間：{_tw.strftime('%Y-%m-%d %H:%M')}（台灣時間）")
        else:
            st.sidebar.caption("🔄 資料庫最後更新時間：尚無資料")
    except Exception:
        st.sidebar.caption("🔄 資料庫最後更新時間：無法讀取")

    st.sidebar.caption("資料來源：PTT MacShop　｜　蝦皮")

# ── Fetch from DB (direct, no FastAPI required) ────────────────────────────────
weights = {
    "ram": ram_mult, "ssd": ssd_mult,
    "air13": w_air13, "air15": w_air15,
    "pro13": w_pro13, "pro14": w_pro14, "pro16": w_pro16,
}

_selected_sources: list = source_filter or []
_source_param = _selected_sources[0] if len(_selected_sources) == 1 else None
_status_param = "sold" if show_sold else "available"

try:
    db = _get_db()
    deals = db.get_filtered_deals(
        status=_status_param,
        chip=chip_input or None,
        ram_gb=ram_gb,
        screen_size=screen_size_filter,
        min_price=float(min_price) if min_price > 0 else None,
        max_price=float(max_price) if max_price > 0 else None,
        model_type=model_type,
        source=_source_param,
    )
    # ssd_gb filter: not in SQL, apply in Python
    if ssd_gb_filter:
        deals = [d for d in deals if int(_nan_safe(d.get("ssd_gb"), 0)) == ssd_gb_filter]

    # All available deals (unfiltered) for p75/p50 baseline
    all_available = db.get_filtered_deals(status="available")
except Exception as exc:
    st.title(":material/laptop: 二手 MacBook 智慧估價系統")
    st.error(
        f"資料庫連線失敗：{exc}\n\n"
        "請確認 `DATABASE_URL` 環境變數已正確設定（本地開發於 `.env`；"
        "Streamlit Cloud 請在 Secrets 設定）。"
    )
    st.stop()

# ── Compute VFM thresholds from ALL available deals (unfiltered) ───────────────
_all_scores = [_recalc_vfm(d, weights) for d in all_available if _nan_safe(d.get("price"), 0) > 0]
p75 = float(np.percentile(_all_scores, 75)) if _all_scores else 350.0
p50 = float(np.percentile(_all_scores, 50)) if _all_scores else 250.0

if not deals:
    st.title(":material/laptop: 二手 MacBook 智慧估價系統")
    st.warning("找不到符合條件的物件。請調整篩選條件後重試。")
    st.stop()

df = pd.DataFrame(deals)
if "source" not in df.columns:
    df["source"] = ""
df["source"] = df["source"].fillna("")

# Client-side source filter (both/none selected)
if len(_selected_sources) == 0:
    st.title(":material/laptop: 二手 MacBook 智慧估價系統")
    st.warning("請至少選擇一個賣場來源（PTT 或 蝦皮）。")
    st.stop()

# ── Recalculate VFM with user weights ─────────────────────────────────────────
df["vfm_score"] = df.apply(lambda r: _recalc_vfm(r.to_dict(), weights), axis=1)
df = df.sort_values("vfm_score", ascending=False).reset_index(drop=True)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title(":material/laptop: 二手 MacBook 智慧估價系統")
prices = df["price"].dropna().astype(float)
try:
    _new_count = _get_db().get_new_count()
except Exception:
    _new_count = 0
_price_range = (
    f"{int(prices.min()):,} ~ {int(prices.max()):,} 元" if len(prices) else "—"
)
st.markdown(f"""
<div class="stat-row">
  <div class="stat"><span>符合物件</span><b>{len(df)} 筆</b></div>
  <div class="stat stat--wide"><span>價格區間</span><b>{_price_range}</b></div>
  <div class="stat"><span>最近新增</span><b>+{_new_count} 筆</b></div>
</div>
""", unsafe_allow_html=True)

# ── 性價比圖例 ───────────────────────────────────────────────────────────────
st.markdown("""
<div class="vfm-legend">
  <span style="font-weight:600;color:#cbd5e1;">性價比</span>
  <span>🟢 <i>優秀</i>（前 25%）</span>
  <span>🟡 <i>普通</i>（前 50%）</span>
  <span>🔴 <i>偏貴</i>（後 50%）</span>
</div>
""", unsafe_allow_html=True)


# ── Score breakdown expander ───────────────────────────────────────────────────
with st.expander(":material/bar_chart: VFM 分數構成 — 點此展開"):
    col_explain, col_chart = st.columns([1, 1])

    with col_explain:
        st.markdown("#### 計算公式")
        st.code("VFM = (晶片基準 × 年份折舊 × RAM加成 × SSD加成 × 形態加成) ÷ 售價 × 1000")
        st.markdown("""
| 因子 | 規則 |
|------|------|
| 晶片基準 | M1=8500 / M2=10000 / M3=11500 / M4=14500（Pro/Max 更高）|
| 年份折舊 | 每年 ×0.9 |
| RAM 加成 | ≥16 GB 時套用 RAM 加權（預設×1.25）|
| SSD 加成 | ≥1 TB 時套用 SSD 加權（預設×1.1）|
| 形態加成 | Air 13"×1.00 / Air 15"×1.08 / Pro 13"×1.00 / Pro 14"×1.18 / Pro 16"×1.22 |
""")
        top = df.iloc[0].to_dict()
        chip_t = str(top.get("chip") or "")
        base_t = _BENCH.get(chip_t, 5000)
        year_t = int(top.get("release_year") or 2020)
        age_t  = max(0, datetime.date.today().year - year_t)
        depr_t = round(0.9 ** age_t, 4)
        r_t    = ram_mult if (top.get("ram_gb") or 0) >= 16   else 1.0
        s_t    = ssd_mult if (top.get("ssd_gb") or 0) >= 1024 else 1.0
        form_t = weights.get(_form_key(top), 1.0)
        adj_t  = base_t * depr_t * r_t * s_t * form_t
        price_t = float(top.get("price") or 1)
        st.markdown(f"**最高分物件分解**（{str(top.get('original_title',''))[:30]}）")
        st.markdown(f"""
| 步驟 | 數值 |
|------|------|
| 晶片基準（{chip_t}）| {base_t:,} |
| ×年份折舊（{age_t} 年）| ×{depr_t} |
| ×RAM 加成 | ×{r_t} |
| ×SSD 加成 | ×{s_t} |
| ×形態加成 | ×{form_t} |
| 調整後分數 | {adj_t:,.0f} |
| ÷售價 {int(price_t):,} ×1000 | **= {round(adj_t/price_t*1000,2)} 分** |
""")

    with col_chart:
        st.markdown("#### 目前加權設定")
        factors = ['Air 13"', 'Air 15"', 'Pro 13"', 'Pro 14"', 'Pro 16"', "RAM", "SSD"]
        values  = [w_air13, w_air15, w_pro13, w_pro14, w_pro16, ram_mult, ssd_mult]
        colors  = ["#818cf8" if "Air" in f else ("#f472b6" if "Pro" in f else "#4ade80") for f in factors]
        fig = go.Figure(go.Bar(
            x=values, y=factors, orientation="h",
            marker_color=colors,
            text=[f"×{v:.2f}" for v in values], textposition="outside",
        ))
        fig.update_layout(
            xaxis=dict(range=[0, max(values) * 1.35], showgrid=False, fixedrange=True),
            yaxis=dict(showgrid=False, fixedrange=True),
            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117", font_color="#fafafa",
            margin=dict(l=10, r=70, t=10, b=10), height=300,
            dragmode=False,
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={"staticPlot": True, "displayModeBar": False},
        )

        st.markdown("#### 晶片基準分參考")
        bench_df = pd.DataFrame(sorted(_BENCH.items(), key=lambda x: -x[1]), columns=["晶片", "基準分"])
        st.dataframe(bench_df, use_container_width=True, hide_index=True, height=210)

st.markdown("")

# ── Pagination state ───────────────────────────────────────────────────────────
PAGE_SIZE = 20

if "page_num" not in st.session_state:
    st.session_state["page_num"] = 1

max_pages = max(1, (len(df) + PAGE_SIZE - 1) // PAGE_SIZE)
current_page = int(st.session_state.get("page_num", 1))
current_page = max(1, min(current_page, max_pages))


# ── Deal cards ─────────────────────────────────────────────────────────────────
# Rendered as one HTML block rather than st.dataframe: a 13-column table is
# unreadable below ~700px and st.dataframe exposes almost no styling control.
_SOURCE_LABELS = {"ptt": "PTT", "shopee": "蝦皮", "carousell": "旋轉"}


def _tier_color(score: float) -> str:
    if score >= p75:
        return "var(--vfm-good)"
    return "var(--vfm-mid)" if score >= p50 else "var(--vfm-poor)"


def _int_or_none(val):
    v = _nan_safe(val, 0)
    try:
        return int(float(v)) or None
    except (TypeError, ValueError):
        return None


def _render_card(row: dict, is_top: bool) -> str:
    score = float(row.get("vfm_score") or 0)
    url = str(row.get("url") or "")
    title = str(row.get("original_title") or "(無標題)")
    source = str(row.get("source") or "")

    # Specs: only emit chips that carry real information.
    specs = []
    chip = str(row.get("chip") or "").strip()
    if chip and chip.lower() != "none":
        specs.append(f'<b class="chip">{escape(chip)}</b>')
    if (ram := _int_or_none(row.get("ram_gb"))):
        specs.append(f"<b>{ram}GB</b>")
    if (ssd := _int_or_none(row.get("ssd_gb"))):
        specs.append(f"<b>{ssd // 1024}TB</b>" if ssd >= 1024 else f"<b>{ssd}GB</b>")
    screen = _nan_safe(row.get("screen_size"), 0)
    if screen:
        specs.append(f'<b>{float(screen):.1f}"</b>')
    if (batt := _int_or_none(row.get("battery_health"))):
        specs.append(f"<b>🔋{batt}%</b>")

    # Footer right: year and location, whichever are known.
    meta = [str(v) for v in (_int_or_none(row.get("release_year")), row.get("location")) if v and str(v) != "未知"]

    price = _nan_safe(row.get("price"), 0)
    price_html = f"<s>NT$</s>{int(float(price)):,}" if price else "—"

    return (
        f'<a class="deal-card{" deal-card--top" if is_top else ""}" href="{escape(url, quote=True)}"'
        f' target="_blank" rel="noopener" title="{escape(title, quote=True)}"'
        f' style="--tier:{_tier_color(score)}">'
        f'<div class="deal-card__head">'
        f'<div class="deal-card__vfm">{score:.0f}<span>CP</span></div>'
        f'<div class="deal-card__src">{escape(_SOURCE_LABELS.get(source, source or "?"))}</div>'
        f'</div>'
        f'<div class="deal-card__title">{escape(title)}</div>'
        f'<div class="deal-card__specs">{"".join(specs)}</div>'
        f'<div class="deal-card__foot">'
        f'<div class="deal-card__price">{price_html}</div>'
        f'<div class="deal-card__meta">{escape(" · ".join(meta))}</div>'
        f'</div>'
        f'</a>'
    )


start = (current_page - 1) * PAGE_SIZE
paginated = df.iloc[start: start + PAGE_SIZE]

cards = [
    _render_card(r.to_dict(), is_top=(current_page == 1 and i == 0))
    for i, (_, r) in enumerate(paginated.iterrows())
]
st.markdown(f'<div class="deal-grid">{"".join(cards)}</div>', unsafe_allow_html=True)

# ── Pagination ─────────────────────────────────────────────────────────────────
st.markdown("")

prev_col, mid_col, next_col = st.columns([2, 3, 2])
with prev_col:
    if st.button("← 上一頁", disabled=(current_page <= 1), key="pg_prev", use_container_width=True):
        st.session_state["page_num"] = current_page - 1
        st.rerun()
with mid_col:
    selected_page = st.selectbox(
        "頁數",
        options=list(range(1, max_pages + 1)),
        index=current_page - 1,
        label_visibility="collapsed",
        key="pg_select",
        format_func=lambda p: f"第 {p} / {max_pages} 頁",
    )
    if selected_page != current_page:
        st.session_state["page_num"] = selected_page
        st.rerun()
with next_col:
    if st.button("下一頁 →", disabled=(current_page >= max_pages), key="pg_next", use_container_width=True):
        st.session_state["page_num"] = current_page + 1
        st.rerun()
st.markdown(
    f"<div style='text-align:center;padding:4px 0;color:#94a3b8;font-size:0.8rem;'>共 {len(df)} 筆</div>",
    unsafe_allow_html=True,
)
