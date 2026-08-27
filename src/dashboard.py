import datetime
import os
from datetime import timedelta
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# One list, used by the sidebar options, the defaults and the reset button.
# Adding a scraper means adding it here and nowhere else in this file.
SOURCES = ["ptt", "shopee", "carousell"]
SOURCE_LABELS = {"ptt": "PTT MacShop", "shopee": "蝦皮", "carousell": "旋轉拍賣"}


def _read_secret(name: str) -> str | None:
    """Streamlit Community Cloud serves config via st.secrets; local runs use env.

    st.secrets raises when no secrets file exists rather than being absent, so
    the lookup has to be guarded rather than checked with hasattr.
    """
    try:
        val = st.secrets.get(name)
    except st.errors.StreamlitSecretNotFoundError:
        val = None
    return val or os.getenv(name)


# ── DATABASE_URL injection ─────────────────────────────────────────────────────
# Must happen before DBManager is first imported/used.
_db_url = _read_secret("DATABASE_URL")
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
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;700;800;900&family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+TC:wght@400;500;700;900&display=swap');

/* ── SainTech Design System tokens ──────────────────────────────────────────
   Values lifted verbatim from the design system project (tokens/colors.css,
   typography.css, layout.css) — do not round or re-pick them here. The brand
   rule that shapes this page: mono is for specs, prices and model numbers;
   Archivo 900 carries display weight; borders beat shadows, and on dark
   surfaces glow beats grey shadow. */
:root {
    --blue-500: #1F48FF;   /* core brand */
    --blue-600: #1539DB;
    --cyan-500: #15E0FF;   /* new / live / highlight */

    --ink-900: #0A0E1A;
    --ink-800: #11162A;
    --ink-700: #1B2238;
    --ink-600: #2B3450;
    --ink-400: #6B768F;
    --ink-200: #C7CCD8;

    --success: #16C76A;
    --warning: #FFB020;
    --danger:  #FF3B47;

    --font-display: "Archivo", "Noto Sans TC", system-ui, sans-serif;
    --font-body:    "Noto Sans TC", "Archivo", system-ui, sans-serif;
    --font-mono:    "JetBrains Mono", ui-monospace, "SFMono-Regular", monospace;

    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 16px;
    --radius-pill: 999px;

    --glow-brand: 0 0 0 1px rgba(31, 72, 255, 0.5), 0 8px 28px rgba(31, 72, 255, 0.35);
    --ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
    --dur-fast: 120ms;
}

/* ── Streamlit chrome ───────────────────────────────────────────────────────
   Colours, fonts and radii come from .streamlit/config.toml, not from here —
   overriding them with CSS loses to Streamlit's own selectors and leaves the
   widgets on the light theme. Only what the theme cannot express lives below. */
/* Streamlit paints headings in textColor; the brand puts them on --text-strong.
   There is no theme option for heading colour, so this is the one place a
   selector is still needed — scoped tightly enough to beat Streamlit's own. */
[data-testid="stHeading"] h1, [data-testid="stHeading"] h2 { color: #FFFFFF; }

h1 {
    /* Break at spaces (after "MacBook" on mobile), never mid-CJK-word. */
    word-break: keep-all;
    overflow-wrap: normal;
    line-height: 1.04;
    letter-spacing: -0.02em;
}
h1 a.anchor-link, h2 a.anchor-link, h3 a.anchor-link { display: none !important; }

/* The logo's S carries a motion dot-trail; as a band it becomes the page's
   masthead rule (base.css .st-speed-stripes). */
.st-stripe {
    height: 5px;
    border-radius: 2px;
    margin: 0 0 14px;
    background-image: repeating-linear-gradient(-60deg, var(--blue-500) 0 14px, var(--blue-600) 14px 28px);
}

.st-eyebrow {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--cyan-500);
    margin-bottom: 2px;
}

/* ── Stat row ─────────────────────────────────────────────────────────────── */
.stat-row { display: flex; flex-wrap: nowrap; gap: 8px; margin: 10px 0 12px; }
.stat {
    flex: 1 1 0;
    min-width: 0;
    padding: 9px 14px;
    background: var(--ink-800);
    border: 1px solid var(--ink-700);
    border-radius: var(--radius-md);
}
.stat--wide { flex: 1.9 1 0; }
.stat span {
    display: block;
    font-family: var(--font-display);
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-400);
    margin-bottom: 2px;
    white-space: nowrap;
}
.stat b {
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 22px;
    color: #FFFFFF;
    white-space: nowrap;
    line-height: 1.1;
}
.stat--new b { color: var(--cyan-500); }

.vfm-legend {
    display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
    padding: 2px 0 10px;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink-400);
}
.vfm-legend i { font-style: normal; }
.vfm-legend b { width: 8px; height: 8px; border-radius: 2px; display: block; }

/* ── Deal rows ────────────────────────────────────────────────────────────
   One item per row rather than a multi-column grid: each row carries only
   score, title, specs and price, so it stays scannable at full width. */
.deal-list { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 18px; }

.deal {
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 16px 20px;
    background: var(--ink-800);
    border: 1px solid var(--ink-700);
    border-radius: var(--radius-lg);
    text-decoration: none !important;
    color: var(--ink-200) !important;
    transition: transform var(--dur-fast) var(--ease-out), filter var(--dur-fast) var(--ease-out);
}
/* Brand motion rule: quick, mechanical, no overshoot. */
.deal:hover { transform: translateY(-3px); filter: brightness(1.08); }
@media (hover: none) { .deal:hover { transform: none; filter: none; } }

/* Rank 1 on page 1 answers the question the product exists to ask. */
.deal--top { border: 2px solid var(--blue-500); box-shadow: var(--glow-brand); }

/* ScorePill anatomy, minus the /max — VFM has no fixed ceiling. */
.deal__score { display: flex; flex-direction: column; align-items: center; gap: 6px; flex-shrink: 0; }
.deal__box {
    width: 68px; height: 68px;
    border-radius: var(--radius-md);
    background: var(--ink-900);
    border: 3px solid var(--tone);
    box-shadow: 0 0 18px var(--tone-glow);
    display: flex; align-items: center; justify-content: center;
}
.deal__box span { font-family: var(--font-display); font-weight: 900; font-size: 26px; color: #FFFFFF; line-height: 1; }
.deal__scorelabel {
    font-family: var(--font-display);
    font-weight: 700; font-size: 10px;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--tone);
}

.deal__body { display: flex; flex-direction: column; gap: 8px; flex-grow: 1; min-width: 0; }
.deal__titlerow { display: flex; align-items: center; gap: 8px; min-width: 0; }
.deal__title {
    font-family: var(--font-body);
    font-weight: 700; font-size: 16px;
    color: #FFFFFF;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.badge {
    flex-shrink: 0;
    display: inline-flex; align-items: center;
    font-family: var(--font-display);
    font-weight: 800; font-size: 11px;
    letter-spacing: 0.06em; text-transform: uppercase;
    padding: 4px 9px; border-radius: 4px; line-height: 1;
    background: var(--blue-500); color: #FFFFFF;
}

.deal__chips { display: flex; flex-wrap: wrap; gap: 6px; }
.deal__chips i {
    font-style: normal;
    display: inline-flex; align-items: center;
    font-family: var(--font-mono);
    font-weight: 500; font-size: 13px; letter-spacing: 0.01em;
    padding: 6px 12px;
    border-radius: var(--radius-pill);
    background: var(--ink-700);
    color: var(--ink-200);
    border: 1px solid var(--ink-600);
}
.deal__chips i.warn { color: var(--warning); }

.deal__buy { display: flex; flex-direction: column; align-items: flex-end; gap: 10px; flex-shrink: 0; }
.deal__price { display: flex; align-items: baseline; gap: 4px; }
.deal__price s { font-family: var(--font-mono); font-weight: 500; font-size: 13px; color: var(--ink-400); text-decoration: none; }
.deal__price b { font-family: var(--font-mono); font-weight: 700; font-size: 27px; color: #FFFFFF; line-height: 1; }
.deal__cta {
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 44px;
    font-family: var(--font-display);
    font-weight: 800; font-size: 15px; letter-spacing: 0.01em;
    padding: 11px 20px;
    border-radius: var(--radius-md);
    background: transparent; color: #FFFFFF;
    border: 2px solid var(--ink-600);
    white-space: nowrap;
}
.deal--top .deal__cta { background: var(--blue-500); border-color: var(--blue-500); }

/* ── Footer ───────────────────────────────────────────────────────────────── */
.st-footer {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    gap: 12px;
    margin-top: 22px; padding-top: 14px;
    border-top: 1px solid var(--ink-700);
    font-family: var(--font-mono); font-size: 12px; color: var(--ink-400);
}
.st-footer a {
    display: inline-flex; align-items: center; gap: 7px;
    font-family: var(--font-display); font-weight: 700; font-size: 12px;
    letter-spacing: 0.06em;
    color: var(--ink-200) !important;
    text-decoration: none !important;
    transition: color var(--dur-fast) var(--ease-out);
}
.st-footer a:hover { color: var(--cyan-500) !important; }
.st-footer a svg { flex-shrink: 0; }

/* ── Below 700px the row folds into a stack ───────────────────────────────── */
@media (max-width: 700px) {
    h1 { font-size: 1.5rem !important; }
    .st-eyebrow { font-size: 10px; }
    .stat { padding: 7px 10px; }
    .stat span { font-size: 9px; letter-spacing: 0.1em; }
    .stat b { font-size: 15px; }
    .stat--wide b { font-size: 12.5px; }
    .vfm-legend { gap: 10px; font-size: 11px; }
    .block-container { padding-top: 2.2rem !important; }

    /* Grid areas rather than a column flex: the chips live inside .deal__body in
       the DOM, but on a phone they need the full card width instead of the
       narrow column beside the score. Dissolving .deal__body with
       `display: contents` promotes its children to grid items so they can be
       placed independently — no duplicate markup for the two layouts. */
    .deal {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        grid-template-areas:
            "score title"
            "chips chips"
            "buy   buy";
        align-items: start;
        gap: 10px 12px;
        padding: 14px;
    }
    .deal__body { display: contents; }
    .deal__score { grid-area: score; }
    .deal__titlerow { grid-area: title; }
    .deal__chips { grid-area: chips; }
    .deal__buy { grid-area: buy; }

    .deal__box { width: 60px; height: 60px; }
    .deal__box span { font-size: 23px; }
    .deal__title { white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; font-size: 14px; line-height: 1.45; }
    .deal__titlerow { flex-wrap: wrap; }
    .deal__chips i { font-size: 11.5px; padding: 5px 10px; }
    .deal__buy {
        flex-direction: row; align-items: center; justify-content: space-between;
        padding-top: 10px; border-top: 1px solid var(--ink-700);
    }
    .deal__price b { font-size: 24px; }
    .deal__cta { font-size: 14px; }
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
            "source_filter": list(SOURCES),
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
        options=SOURCES,
        format_func=lambda x: SOURCE_LABELS.get(x, x),
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
            "source_filter": list(SOURCES),
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
# get_filtered_deals takes a single source, so only that case goes to SQL.
# Any other subset is narrowed in Python below — previously a two-of-three
# selection applied no filter at all and leaked the unselected source.
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
    # Subset of sources that SQL could not express
    if 1 < len(_selected_sources) < len(SOURCES):
        deals = [d for d in deals if d.get("source") in _selected_sources]

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
st.markdown('<div class="st-stripe"></div><div class="st-eyebrow">SainTech · 二手行情</div>',
            unsafe_allow_html=True)
st.title("二手 MacBook 估價")
prices = df["price"].dropna().astype(float)
try:
    _new_count = _get_db().get_new_count()
except Exception:
    _new_count = 0
_price_range = (
    f"{int(prices.min()):,}–{int(prices.max()):,}" if len(prices) else "—"
)
st.markdown(f"""
<div class="stat-row">
  <div class="stat"><span>在售</span><b>{len(df)}</b></div>
  <div class="stat stat--wide"><span>價格區間</span><b>{_price_range}</b></div>
  <div class="stat stat--new"><span>今日新增</span><b>+{_new_count}</b></div>
</div>
""", unsafe_allow_html=True)

# ── 性價比圖例 ───────────────────────────────────────────────────────────────
# Squares in the semantic colours, not emoji — the brand does not use emoji.
st.markdown("""
<div class="vfm-legend">
  <span style="display:flex;gap:7px;align-items:center;"><b style="background:var(--success);"></b><i>優秀</i> 前 25%</span>
  <span style="display:flex;gap:7px;align-items:center;"><b style="background:var(--warning);"></b><i>普通</i> 前 50%</span>
  <span style="display:flex;gap:7px;align-items:center;"><b style="background:var(--danger);"></b><i>偏貴</i> 後 50%</span>
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
# Short forms for the card's "前往X" button; SOURCE_LABELS at the top holds the
# longer names the sidebar filter shows.
_CTA_LABELS = {"ptt": "PTT", "shopee": "蝦皮", "carousell": "旋轉拍賣"}


def _tier(score: float) -> tuple[str, str]:
    """Return (border colour, glow colour) for the score's verdict band.

    The glow is the same hue at 33% alpha, matching ScorePill's `${tone}55`.
    """
    if score >= p75:
        return "var(--success)", "#16C76A55"
    if score >= p50:
        return "var(--warning)", "#FFB02055"
    return "var(--danger)", "#FF3B4755"


def _int_or_none(val):
    v = _nan_safe(val, 0)
    try:
        return int(float(v)) or None
    except (TypeError, ValueError):
        return None


def _render_deal(row: dict, is_top: bool) -> str:
    score = float(row.get("vfm_score") or 0)
    url = str(row.get("url") or "")
    title = str(row.get("original_title") or "(無標題)")
    source = str(row.get("source") or "")
    tone, glow = _tier(score)

    # Chips: only emit what carries real information.
    chips = []
    chip = str(row.get("chip") or "").strip()
    if chip and chip.lower() != "none":
        chips.append(f"<i>{escape(chip)}</i>")
    ram, ssd = _int_or_none(row.get("ram_gb")), _int_or_none(row.get("ssd_gb"))
    if ram and ssd:
        ssd_txt = f"{ssd // 1024}TB" if ssd >= 1024 else f"{ssd}GB"
        chips.append(f"<i>{ram}GB / {ssd_txt}</i>")
    elif ram:
        chips.append(f"<i>{ram}GB</i>")
    elif ssd:
        chips.append(f"<i>{ssd // 1024}TB</i>" if ssd >= 1024 else f"<i>{ssd}GB</i>")
    if (screen := _nan_safe(row.get("screen_size"), 0)):
        chips.append(f'<i>{float(screen):.1f}"</i>')
    if (year := _int_or_none(row.get("release_year"))):
        chips.append(f"<i>{year}</i>")
    if (batt := _int_or_none(row.get("battery_health"))):
        # Below 85% the battery is a caution, not a neutral spec.
        chips.append(f'<i class="warn">電池 {batt}%</i>' if batt < 85 else f"<i>電池 {batt}%</i>")
    # PTT sellers write whole sentences into the location field. Truncate with an
    # ellipsis so it reads as shortened rather than as a chip cut off mid-word.
    loc = str(row.get("location") or "").strip().lstrip("-— ").strip()
    if loc and loc != "未知":
        chips.append(f"<i>{escape(loc if len(loc) <= 12 else loc[:12] + '…')}</i>")

    price = _nan_safe(row.get("price"), 0)
    price_html = (
        f"<s>NT$</s><b>{int(float(price)):,}</b>" if price else "<b>—</b>"
    )
    cta = f"前往{_CTA_LABELS.get(source, source or '賣場')}"
    badge = '<span class="badge">最划算</span>' if is_top else ""

    return (
        f'<a class="deal{" deal--top" if is_top else ""}" href="{escape(url, quote=True)}"'
        f' target="_blank" rel="noopener" title="{escape(title, quote=True)}"'
        f' style="--tone:{tone};--tone-glow:{glow}">'
        f'<div class="deal__score">'
        f'<div class="deal__box"><span>{score:.0f}</span></div>'
        f'<div class="deal__scorelabel">CP 值</div>'
        f'</div>'
        f'<div class="deal__body">'
        f'<div class="deal__titlerow">{badge}<div class="deal__title">{escape(title)}</div></div>'
        f'<div class="deal__chips">{"".join(chips)}</div>'
        f'</div>'
        f'<div class="deal__buy">'
        f'<div class="deal__price">{price_html}</div>'
        f'<div class="deal__cta">{escape(cta)}</div>'
        f'</div>'
        f'</a>'
    )


start = (current_page - 1) * PAGE_SIZE
paginated = df.iloc[start: start + PAGE_SIZE]

deals = [
    _render_deal(r.to_dict(), is_top=(current_page == 1 and i == 0))
    for i, (_, r) in enumerate(paginated.iterrows())
]
st.markdown(f'<div class="deal-list">{"".join(deals)}</div>', unsafe_allow_html=True)

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
    f"<div style='text-align:center;padding:4px 0;color:var(--ink-400);"
    f"font-family:var(--font-mono);font-size:12px;'>共 {len(df)} 筆</div>",
    unsafe_allow_html=True,
)

# ── Footer ─────────────────────────────────────────────────────────────────────
# The channel link is opt-in via SAINTECH_URL so a fork or a local run does not
# advertise someone else's site. Icon is inline SVG — the brand uses no emoji.
_SAINTECH_URL = (_read_secret("SAINTECH_URL") or "").strip()
_ARROW_SVG = (
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.5" stroke-linecap="square"><path d="M7 17 17 7"/><path d="M8 7h9v9"/></svg>'
)
_link_html = (
    f'<a href="{escape(_SAINTECH_URL, quote=True)}" target="_blank" rel="noopener">'
    f'SainTech 頻道{_ARROW_SVG}</a>'
    if _SAINTECH_URL else ""
)
st.markdown(
    f'<div class="st-footer">'
    f'<div>資料來源　PTT MacShop · 蝦皮購物</div>'
    f'{_link_html}'
    f'</div>',
    unsafe_allow_html=True,
)
