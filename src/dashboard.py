import datetime
import os
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

_BENCH = {
    "M1": 8500, "M1 Pro": 12000, "M1 Max": 12500,
    "M2": 10000, "M2 Pro": 14000, "M2 Max": 14500,
    "M3": 11500, "M3 Pro": 15500, "M3 Max": 21000,
    "M4": 14500, "M4 Pro": 22000, "M4 Max": 26000,
}


# 預設機型×螢幕組合加權
_DEFAULT_FORM_W = {
    "air13":  1.00,
    "air15":  1.08,
    "pro13":  1.00,
    "pro14":  1.18,
    "pro16":  1.22,
}


def _form_key(row: dict) -> str:
    """依 series + screen_size 決定形態 key。"""
    s  = str(row.get("series") or "").lower()
    sc = float(row.get("screen_size") or 13.3)
    if "air" in s:
        return "air15" if sc >= 15.0 else "air13"
    # Pro
    if sc >= 15.0:
        return "pro16"
    if sc >= 14.0:
        return "pro14"
    return "pro13"


def _recalc_vfm(row: dict, w: dict) -> float:
    price = float(row.get("price") or 0)
    if price <= 0:
        return 0.0
    chip  = str(row.get("chip") or "")
    base  = _BENCH.get(chip, 5000)
    year  = int(row.get("release_year") or 2020)
    age   = max(0, datetime.date.today().year - year)
    depr  = 0.9 ** age
    ram_m  = w["ram"] if (row.get("ram_gb") or 0) >= 16   else 1.0
    ssd_m  = w["ssd"] if (row.get("ssd_gb") or 0) >= 1024 else 1.0
    form_m = w.get(_form_key(row), 1.0)
    return round(base * depr * ram_m * ssd_m * form_m / price * 1000, 2)


def _vfm_badge(score: float, p75: float, p50: float) -> str:
    """回傳帶顏色 emoji 的分數字串。"""
    if score >= p75:
        return f"🟢 {score:.2f}"
    elif score >= p50:
        return f"🟡 {score:.2f}"
    else:
        return f"🔴 {score:.2f}"


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="mac-valuer | 二手 MacBook 估價", page_icon="💻", layout="wide")

st.markdown("""
<style>
[data-testid="stMetric"] { background:#1e1e2e; border-radius:10px; padding:12px 16px; }
[data-testid="stSidebarContent"] { background:#16162a; }

/* 分頁按鈕群組 */
div[data-testid="stHorizontalBlock"] .page-btn button {
    border-radius: 6px;
    font-weight: 600;
}

/* 前往連結按鈕 */
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
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔍 篩選條件")

    # 統一初始化所有預設值，避免 widget value 衝突
    if "min_price" not in st.session_state:
        st.session_state.update({
            "model_type": None, "chip_input": "", "ram_gb": None,
            "ssd_gb_filter": None, "screen_size": None,
            "min_price": 0, "max_price": 0, "show_sold": False,
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
    show_sold = st.checkbox("顯示已售出物件", key="show_sold")

    st.divider()

    def _reset_vfm_weights():
        st.session_state.update({
            "ram_mult": 1.25, "ssd_mult": 1.1,
            "w_air13": 1.00, "w_air15": 1.08,
            "w_pro13": 1.00, "w_pro14": 1.18, "w_pro16": 1.22,
        })

    with st.expander("⚙️ VFM 評分設定"):
        st.caption("調整各項規格加權，分數即時重算")
        ram_mult = st.slider("RAM 加權（≥ 16 GB）", min_value=1.0, max_value=2.0, step=0.05, key="ram_mult")
        ssd_mult = st.slider("SSD 加權（≥ 1 TB）", min_value=1.0, max_value=2.0, step=0.05, key="ssd_mult")
        st.caption("💻 機型 × 螢幕組合加權")
        w_air13 = st.slider('Air 13"',  min_value=0.5, max_value=2.0, step=0.05, key="w_air13")
        w_air15 = st.slider('Air 15"',  min_value=0.5, max_value=2.0, step=0.05, key="w_air15")
        w_pro13 = st.slider('Pro 13"',  min_value=0.5, max_value=2.0, step=0.05, key="w_pro13")
        w_pro14 = st.slider('Pro 14"',  min_value=0.5, max_value=2.0, step=0.05, key="w_pro14")
        w_pro16 = st.slider('Pro 16"',  min_value=0.5, max_value=2.0, step=0.05, key="w_pro16")
        st.button("↺ 重置評分設定", on_click=_reset_vfm_weights, use_container_width=True)

    st.divider()

    def _reset_all():
        st.session_state.update({
            "model_type": None, "chip_input": "", "ram_gb": None,
            "ssd_gb_filter": None, "screen_size": None,
            "min_price": 0, "max_price": 0, "show_sold": False,
            "ram_mult": 1.25, "ssd_mult": 1.1,
            "w_air13": 1.00, "w_air15": 1.08,
            "w_pro13": 1.00, "w_pro14": 1.18, "w_pro16": 1.22,
            "page_num": 1,
        })

    st.sidebar.button("重置", use_container_width=True, on_click=_reset_all)
    st.sidebar.caption(f"資料來源：PTT MacShop　｜　API：{API_BASE}")

# ── Fetch ──────────────────────────────────────────────────────────────────────
params: dict = {"status": "sold" if show_sold else "available"}
if chip_input:         params["chip"]        = chip_input
if ram_gb:             params["ram_gb"]      = ram_gb
if ssd_gb_filter:      params["ssd_gb"]      = ssd_gb_filter
if screen_size_filter: params["screen_size"] = screen_size_filter
if min_price > 0:      params["min_price"]   = min_price
if max_price > 0:      params["max_price"]   = max_price
if model_type:         params["model_type"]  = model_type

try:
    resp = requests.get(f"{API_BASE}/api/deals", params=params, timeout=10)
    resp.raise_for_status()
    deals = resp.json().get("deals", [])
except requests.exceptions.ConnectionError:
    st.title("💻 二手 MacBook 智慧估價系統")
    st.error(f"無法連線到 API（{API_BASE}）。請先執行：`uvicorn api.main:app --reload --port 8000`")
    st.stop()
except Exception as exc:
    st.error(f"API 錯誤：{exc}")
    st.stop()

if not deals:
    st.title("💻 二手 MacBook 智慧估價系統")
    st.warning("找不到符合條件的物件。請調整篩選條件後重試。")
    st.stop()

df = pd.DataFrame(deals)

# ── Recalculate VFM with user weights ─────────────────────────────────────────
weights = {
    "ram": ram_mult, "ssd": ssd_mult,
    "air13": w_air13, "air15": w_air15,
    "pro13": w_pro13, "pro14": w_pro14, "pro16": w_pro16,
}
df["vfm_score"] = df.apply(lambda r: _recalc_vfm(r.to_dict(), weights), axis=1)
df = df.sort_values("vfm_score", ascending=False).reset_index(drop=True)

# ── VFM 百分位數閾值：使用全庫資料，不受篩選影響 ─────────────────────────────
@st.cache_data(ttl=300)
def _fetch_all_scores(_weights_key: str, status: str) -> tuple:
    try:
        r = requests.get(f"{API_BASE}/api/deals", params={"status": status}, timeout=10)
        r.raise_for_status()
        all_deals = r.json().get("deals", [])
        if not all_deals:
            return 350.0, 250.0
        all_df = pd.DataFrame(all_deals)
        all_df["vfm_score"] = all_df.apply(lambda row: _recalc_vfm(row.to_dict(), weights), axis=1)
        return float(all_df["vfm_score"].quantile(0.75)), float(all_df["vfm_score"].quantile(0.50))
    except Exception:
        return 350.0, 250.0

_weights_key = "|".join(f"{k}={v}" for k, v in sorted(weights.items()))
_status_key  = "sold" if show_sold else "available"
p75, p50 = _fetch_all_scores(_weights_key, _status_key)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("💻 二手 MacBook 智慧估價系統")
prices = df["price"].dropna().astype(float)

m1, m2 = st.columns(2)
with m1:
    st.metric("符合物件", f"{len(df)} 筆")
with m2:
    st.metric(
        "價格區間",
        f"{int(prices.min()):,} ~ {int(prices.max()):,} 元" if len(prices) else "-"
    )

# ── VFM 顏色圖例說明 ─────────────────────────────────────────────────────────
st.markdown(f"""
<div style="display:flex;gap:20px;align-items:center;padding:8px 0 4px;font-size:0.88rem;color:#94a3b8;">
  <span>VFM 分數說明：</span>
  <span>🟢 <b>優秀</b>（前 25%，≥ {p75:.1f} 分）</span>
  <span>🟡 <b>普通</b>（前 50%，≥ {p50:.1f} 分）</span>
  <span>🔴 <b>偏貴</b>（後 50%）</span>
</div>
""", unsafe_allow_html=True)
st.markdown("")


# ── Score breakdown expander ───────────────────────────────────────────────────
with st.expander("📊 VFM 分數構成 — 點此展開"):
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

# ── Table header + page size ────────────────────────────────────────────────────
if "page_num" not in st.session_state:
    st.session_state["page_num"] = 1

hcol, pcol = st.columns([5, 1])
with hcol:
    st.subheader(f"完整列表（{len(df)} 筆）")
with pcol:
    page_size = st.selectbox("每頁顯示", [10, 20, 50], key="page_size")

max_pages = max(1, (len(df) + page_size - 1) // page_size)
current_page = int(st.session_state.get("page_num", 1))
current_page = max(1, min(current_page, max_pages))



# ── Data table ─────────────────────────────────────────────────────────────────
start = (current_page - 1) * page_size
paginated = df.iloc[start: start + page_size].copy()

# 加入 VFM 顏色標籤欄
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
        "前往": st.column_config.LinkColumn(
            "前往",
            display_text="🔗 前往",
            width="small",
        ),
        "價格 (TWD)": st.column_config.NumberColumn(format="%d"),
        "電池健康":   st.column_config.NumberColumn(format="%d %%"),
    },
    hide_index=True,
)

# ── Pagination — 頁碼按鈕列（列表下方）───────────────────────────────────────
st.markdown("")

def _page_range(current: int, total: int, window: int = 2) -> list:
    pages: list = [1]
    lo = max(2, current - window)
    hi = min(total - 1, current + window)
    if lo > 2:
        pages.append("…")
    pages.extend(range(lo, hi + 1))
    if hi < total - 1:
        pages.append("…")
    if total > 1:
        pages.append(total)
    return pages

if max_pages > 1:
    page_slots = _page_range(current_page, max_pages)
    btn_cols   = st.columns([1] * (len(page_slots) + 2))

    with btn_cols[0]:
        if st.button("‹", disabled=(current_page <= 1), key="pg_prev"):
            st.session_state["page_num"] = current_page - 1
            st.rerun()

    for i, slot in enumerate(page_slots):
        with btn_cols[i + 1]:
            if slot == "…":
                st.markdown("<div style='text-align:center;line-height:2.4;color:#555'>…</div>",
                            unsafe_allow_html=True)
            else:
                label = f"**{slot}**" if slot == current_page else str(slot)
                if st.button(label, key=f"pg_{slot}"):
                    st.session_state["page_num"] = slot
                    st.rerun()

    with btn_cols[-1]:
        if st.button("›", disabled=(current_page >= max_pages), key="pg_next"):
            st.session_state["page_num"] = current_page + 1
            st.rerun()

st.caption(f"第 {current_page} 頁，共 {max_pages} 頁　｜　{len(df)} 筆物件")
