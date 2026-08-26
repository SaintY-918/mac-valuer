import datetime
import os
from datetime import timedelta

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


def _vfm_badge(score: float, p75: float, p50: float) -> str:
    if score >= p75:
        return f"🟢 {score:.2f}"
    elif score >= p50:
        return f"🟡 {score:.2f}"
    else:
        return f"🔴 {score:.2f}"


@st.cache_resource
def _get_db() -> DBManager:
    return DBManager()


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="mac-valuer | 二手 MacBook 估價", page_icon=":material/laptop:", layout="wide")

st.markdown("""
<style>
[data-testid="stMetric"] { background:#1e1e2e; border-radius:10px; padding:12px 16px; }
[data-testid="stSidebarContent"] { background:#16162a; }

div[data-testid="stHorizontalBlock"] .page-btn button {
    border-radius: 6px;
    font-weight: 600;
}

a.deal-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    border-radius: 6px;
    background: linear-gradient(135deg, #3b82f6, #6366f1);
    color: white !important;
    font-size: 0.82rem;
    font-weight: 600;
    text-decoration: none;
    transition: opacity .15s;
}
a.deal-link:hover { opacity: 0.82; }

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
<div style="display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 12px;">
  <div style="background:#1e1e2e;border-radius:10px;padding:10px 18px;flex:1;min-width:100px;">
    <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:2px;">符合物件</div>
    <div style="font-size:1.4rem;font-weight:700;">{len(df)} 筆</div>
  </div>
  <div style="background:#1e1e2e;border-radius:10px;padding:10px 18px;flex:2;min-width:160px;">
    <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:2px;">價格區間</div>
    <div style="font-size:1.1rem;font-weight:700;">{_price_range}</div>
  </div>
  <div style="background:#1e1e2e;border-radius:10px;padding:10px 18px;flex:1;min-width:100px;">
    <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:2px;">最近新增</div>
    <div style="font-size:1.4rem;font-weight:700;">+{_new_count} 筆</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── 性價比圖例 ───────────────────────────────────────────────────────────────
st.markdown("""
<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:4px 0 8px;font-size:0.85rem;color:#94a3b8;">
  <span style="font-weight:600;color:#cbd5e1;">性價比</span>
  <span>🟢 <b>優秀</b>（前 25%）</span>
  <span>🟡 <b>普通</b>（前 50%）</span>
  <span>🔴 <b>偏貴</b>（後 50%）</span>
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

# ── Table header ───────────────────────────────────────────────────────────────
PAGE_SIZE = 20

if "page_num" not in st.session_state:
    st.session_state["page_num"] = 1

max_pages = max(1, (len(df) + PAGE_SIZE - 1) // PAGE_SIZE)
current_page = int(st.session_state.get("page_num", 1))
current_page = max(1, min(current_page, max_pages))


# ── Data table ─────────────────────────────────────────────────────────────────
start = (current_page - 1) * PAGE_SIZE
paginated = df.iloc[start: start + PAGE_SIZE].copy()

paginated["CP 值"] = paginated["vfm_score"].apply(lambda s: _vfm_badge(s, p75, p50))

display_cols = {
    "url": "前往", "original_title": "標題", "chip": "晶片",
    "ram_gb": "RAM (GB)", "ssd_gb": "SSD (GB)", "screen_size": "螢幕吋",
    "release_year": "年份", "price": "價格 (TWD)", "location": "地區",
    "battery_health": "電池健康", "warranty_status": "保固",
    "condition": "成色", "CP 值": "CP 值",
}
avail = [c for c in display_cols if c in paginated.columns]
display_df = paginated[avail].copy().rename(columns=display_cols)

if "年份" in display_df.columns:
    display_df["年份"] = display_df["年份"].apply(
        lambda x: str(int(x)) if pd.notna(x) and x else "-"
    )
if "螢幕吋" in display_df.columns:
    display_df["螢幕吋"] = display_df["螢幕吋"].apply(
        lambda x: f"{float(x):.1f}\"" if pd.notna(x) and x else "-"
    )

st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        "前往":       st.column_config.LinkColumn("前往", display_text="🔗", width="small"),
        "標題":       st.column_config.TextColumn("標題", disabled=True),
        "晶片":       st.column_config.TextColumn("晶片", disabled=True),
        "地區":       st.column_config.TextColumn("地區", disabled=True),
        "保固":       st.column_config.TextColumn("保固", disabled=True),
        "成色":       st.column_config.TextColumn("成色", disabled=True),
        "價格 (TWD)": st.column_config.NumberColumn("價格 (TWD)", format="%d"),
        "電池健康":   st.column_config.NumberColumn("電池健康", format="%d%%"),
    },
    hide_index=True,
)

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
