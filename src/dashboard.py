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

from src.calculator.score_engine import (
    RAM_BONUS_THRESHOLD_GB,
    SSD_BONUS_THRESHOLD_GB,
    ScoringWeights,
    current_year,
    depreciation,
    form_factor_key,
    nominal_inches,
    vfm_from_mapping,
)
from src.database.db_manager import DBManager
from src.models.mac_spec import VALID_RAM_GB, VALID_SSD_GB
from src.parser.condition_flags import defects_for
from src.utils.benchmark_db import CHIP_BENCHMARKS, get_benchmark

# Scoring lives in src/calculator/score_engine — see the note there. The
# dashboard used to keep its own copy of the formula and benchmark table, and
# the two drifted apart.
_BENCH = CHIP_BENCHMARKS

# Slider defaults are read off ScoringWeights rather than retyped, so the page
# opens on exactly the weights the backend scores with.
_W = ScoringWeights()
DEFAULT_SLIDERS = {
    "ram_mult": _W.ram_multiplier, "ssd_mult": _W.ssd_multiplier,
    "w_air13": _W.form_air13, "w_air15": _W.form_air15,
    "w_pro13": _W.form_pro13, "w_pro14": _W.form_pro14, "w_pro16": _W.form_pro16,
}


def _nan_safe(val, default):
    """DataFrame cells arrive as NaN rather than None, and NaN is truthy."""
    import math
    if val is None:
        return default
    try:
        if math.isnan(float(val)):
            return default
    except (TypeError, ValueError):
        pass
    return val or default


@st.cache_resource
def _get_db() -> DBManager:
    return DBManager()


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Mac 好價雷達", page_icon=":material/laptop:", layout="wide")

st.markdown("""
<style>
/* ── Tokens ─────────────────────────────────────────────────────────────────
   Unbranded. This page used to carry the SainTech blue and Archivo 900, which
   were inherited from another project rather than chosen for a list of prices.
   Colours and fonts that Streamlit's own widgets need live in
   .streamlit/config.toml; only what the theme cannot express is below. */
:root {
    --ground:    #F5F6F8;   /* cool off-white, never cream */
    --surface:   #FFFFFF;
    --line:      #E4E7EC;
    --line-soft: #EDEFF2;

    --ink:       #111418;   /* near-black, biased blue rather than neutral */
    --ink-soft:  #5B6470;
    --ink-faint: #8E97A2;

    --accent:    #1A6ACF;

    /* The verdict bands, and the page's only saturated colour. Muted on
       purpose: a row carries a score, a price and sometimes a defect flag,
       and three loud colours would leave none of them dominant. */
    --good: #2E7D5B;
    --mid:  #A8761F;
    --low:  #9E4F55;

    --font-ui: "Inter", "Noto Sans TC", system-ui, -apple-system, sans-serif;

    --radius: 8px;
    --ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
    --dur-fast: 120ms;
}

/* Figures line up in columns throughout — prices down the right edge, scores
   down the left. Inter's tabular set does what a monospaced face was doing
   before, without switching typeface mid-page. */
.deal, .stat, .deal-caption { font-variant-numeric: tabular-nums; }

h1 {
    word-break: keep-all;      /* break at spaces, never mid-CJK-word */
    overflow-wrap: normal;
    line-height: 1.1;
    letter-spacing: -0.03em;
}
h1 a.anchor-link, h2 a.anchor-link, h3 a.anchor-link { display: none !important; }

.st-eyebrow {
    font-family: var(--font-ui);
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 0.04em;
    /* No uppercase: this line carries platform names, and "PTT MacShop" is not
       "PTT MACSHOP". Small caps on a proper noun reads as shouting. */
    color: var(--accent);
    margin-bottom: 5px;
}

/* ── Stat row ─────────────────────────────────────────────────────────────── */
.stat-row { display: flex; flex-wrap: nowrap; gap: 8px; margin: 12px 0 14px; }
.stat {
    flex: 1 1 0;
    min-width: 0;
    padding: 10px 14px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
}
.stat--wide { flex: 1.9 1 0; }
.stat span {
    display: block;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 3px;
    white-space: nowrap;
}
.stat b {
    font-weight: 600;
    font-size: 21px;
    letter-spacing: -0.02em;
    color: var(--ink);
    white-space: nowrap;
    line-height: 1.15;
}
.stat--new b { color: var(--accent); }

.vfm-legend {
    display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
    padding: 2px 0 12px;
    font-size: 12px;
    color: var(--ink-faint);
}
.vfm-legend i { font-style: normal; }
.vfm-legend b { width: 8px; height: 8px; border-radius: 2px; display: block; }

/* ── Result rows ────────────────────────────────────────────────────────────
   Hairline-separated rows rather than cards, the way a flight or price search
   presents results: the eye runs down one column of prices and nothing boxes
   each listing off from its neighbours. The previous card grid put every
   field at the same weight, so nothing told the reader where to look. */
.deal-list {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    overflow: hidden;
    margin: 0 0 18px;
}

/* Says what the number is, once, instead of repeating a unit on all twenty
   rows — and gives the median somewhere to sit. A score means nothing on its
   own; knowing the middle of the set is what makes 750 read as high. */
.deal-caption {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    padding: 11px 18px;
    background: #FBFCFD;
    border-bottom: 1px solid var(--line-soft);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--ink-faint);
}

.deal {
    display: grid;
    grid-template-columns: 58px minmax(0, 1fr) auto;
    gap: 18px;
    align-items: center;
    padding: 15px 18px;
    border-bottom: 1px solid var(--line-soft);
    text-decoration: none !important;
    color: var(--ink) !important;
    transition: background var(--dur-fast) var(--ease-out);
}
.deal:last-child { border-bottom: 0; }
.deal:hover { background: #FAFBFC; }
.deal:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }

.deal__score { text-align: center; }
.deal__box span {
    display: block;
    font-size: 19px;
    font-weight: 700;
    line-height: 1.15;
    letter-spacing: -0.02em;
    color: var(--tone);
}
.deal__scorelabel {
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-faint);
}

.deal__body { min-width: 0; }

/* What the listing is, as the system understands it — not how it was
   advertised. Seller titles carry shop names, coupon slogans and stock codes,
   and the model is buried somewhere inside. */
.deal__model {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: 1.35;
}
/* The seller's own words, kept but demoted: it is the only way to check the
   line above, and the only place a detail nobody parsed can still show up. */
.deal__title {
    font-size: 12px;
    color: var(--ink-soft);
    margin-top: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.deal__chips {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 5px;
    font-size: 11.5px;
    color: var(--ink-soft);
}
.deal__chips i { font-style: normal; }
.deal__chips i.warn { color: var(--low); font-weight: 500; }

.deal__buy { text-align: right; }
.deal__price {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.025em;
    white-space: nowrap;
}
.deal__price s { text-decoration: none; font-size: 11px; font-weight: 400; color: var(--ink-faint); margin-right: 3px; }
.deal__cta {
    font-size: 10px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-top: 2px;
}

.badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--good);
    border: 1px solid rgba(46, 125, 91, 0.35);
    background: rgba(46, 125, 91, 0.07);
    padding: 0 5px;
    border-radius: 3px;
    margin-right: 6px;
    vertical-align: 1px;
}
/* Sits before "最划算". The formula rewards a low price, and a broken machine
   is cheap because it is broken — so the warning has to outrank the praise. */
.badge--warn {
    color: var(--low);
    border-color: rgba(158, 79, 85, 0.35);
    background: rgba(158, 79, 85, 0.07);
}

@media (max-width: 640px) {
    .deal {
        grid-template-columns: 48px minmax(0, 1fr);
        row-gap: 8px;
        padding: 13px 14px;
    }
    /* Price moves under the model rather than off the edge; it stays the
       heaviest thing in the row either way. */
    .deal__buy {
        grid-column: 2;
        text-align: left;
        display: flex;
        align-items: baseline;
        gap: 10px;
    }
    .deal__cta { margin-top: 0; }
    .deal-caption { padding: 9px 14px; font-size: 9.5px; }
    /* The first listing has to be visible without scrolling — the whole
       complaint that started this redesign was that the phone view was
       unreadable, and a masthead that fills the screen is the same problem in
       a nicer typeface. Heading capped, stats two-up, price range last
       because it is the only one whose digits cannot fit half a 320px row. */
    h1 { font-size: clamp(26px, 7vw, 34px) !important; }
    .stat-row { flex-wrap: wrap; gap: 6px; }
    .stat { flex: 1 1 calc(50% - 3px); padding: 8px 12px; }
    .stat b { font-size: 18px; }
    .stat--wide { order: 3; flex: 1 1 100%; }
    .vfm-legend { gap: 10px; font-size: 11px; padding-bottom: 10px; }
}

/* ── Footer ─────────────────────────────────────────────────────────────── */
.st-footer {
    margin-top: 40px;
    padding-top: 18px;
    border-top: 1px solid var(--line);
    font-size: 12px;
    color: var(--ink-faint);
    display: flex;
    flex-wrap: wrap;
    gap: 6px 18px;
    align-items: center;
}
.st-footer a { color: var(--accent); text-decoration: none; }
.st-footer a:hover { text-decoration: underline; }

/* Wide layout, but not unbounded. At 1440px the row stretched the full window
   and left several hundred pixels of nothing between the model and its price,
   so the eye had to travel the whole way to pair them up. Comparison sites cap
   the result column for the same reason. */
[data-testid="stMainBlockContainer"] {
    max-width: 1120px;
}

/* ── Sidebar affordance ───────────────────────────────────────────────────
   Collapsed, the sidebar leaves only a chevron, and nothing says the filters
   are behind it. Streamlit has no option for labelling it, so the label is
   attached to the control itself — it exists only while the sidebar is shut,
   which is exactly when it is needed. */
[data-testid="stExpandSidebarButton"] {
    width: auto !important;
    padding: 0 10px 0 6px !important;
    display: inline-flex !important;
    align-items: center;
    gap: 5px;
    border: 1px solid var(--line) !important;
    border-radius: var(--radius) !important;
    background: var(--surface) !important;
    color: var(--ink-soft) !important;
}
[data-testid="stExpandSidebarButton"]::after {
    content: "篩選條件";
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
}
[data-testid="stExpandSidebarButton"]:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
}

/* Keep the pagination row horizontal at every width. */
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
            "hide_defects": False,
            "source_filter": list(SOURCES),
            **DEFAULT_SLIDERS,
            "page_num": 1,
        })

    model_type = st.selectbox(
        "機型",
        [None, "Air", "Pro", "Neo"],
        format_func=lambda x: "不限" if x is None else f"MacBook {x}",
        key="model_type",
    )
    chip_input = st.text_input("晶片型號（模糊）", placeholder="例如：M3", key="chip_input")
    ram_gb = st.selectbox(
        "RAM 大小",
        # Derived, not retyped. These lists stopped at 64 GB and 2 TB while the
        # parser already accepted 128 GB and 8 TB, so the newest and priciest
        # machines could not be filtered for at all.
        [None, *VALID_RAM_GB],
        format_func=lambda x: "不限" if x is None else f"{x} GB",
        key="ram_gb",
    )
    ssd_gb_filter = st.selectbox(
        "SSD 大小",
        [None, *VALID_SSD_GB],
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
    hide_defects = st.checkbox("隱藏瑕疵品", key="hide_defects",
                               help="標題或成色提到功能性瑕疵者（瑕疵機、螢幕破裂、C 級等）")

    st.divider()

    def _reset_vfm_weights():
        st.session_state.update({
            **DEFAULT_SLIDERS,
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
            "hide_defects": False,
            "source_filter": list(SOURCES),
            **DEFAULT_SLIDERS,
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

    # Derived from SOURCE_LABELS. Two hand-written copies of this list had both
    # been left behind when Carousell was added — exactly what the note at the
    # top of this file says must not happen.
    st.sidebar.caption("資料來源：" + "　｜　".join(SOURCE_LABELS[s] for s in SOURCES))

# ── Fetch from DB (direct, no FastAPI required) ────────────────────────────────
# The sliders write straight into the shared weight model, so the page and the
# backend cannot drift apart again.
weights = ScoringWeights(
    ram_multiplier=ram_mult, ssd_multiplier=ssd_mult,
    form_air13=w_air13, form_air15=w_air15,
    form_pro13=w_pro13, form_pro14=w_pro14, form_pro16=w_pro16,
)

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

    if hide_defects:
        deals = [d for d in deals if not defects_for(d)]

    # ssd_gb filter: not in SQL, apply in Python
    if ssd_gb_filter:
        deals = [d for d in deals if int(_nan_safe(d.get("ssd_gb"), 0)) == ssd_gb_filter]

    # All available deals (unfiltered) for p75/p50 baseline
    all_available = db.get_filtered_deals(status="available")
except Exception as exc:
    st.title("Mac 好價雷達")
    st.error(
        f"資料庫連線失敗：{exc}\n\n"
        "請確認 `DATABASE_URL` 環境變數已正確設定（本地開發於 `.env`；"
        "Streamlit Cloud 請在 Secrets 設定）。"
    )
    st.stop()

# ── Compute VFM thresholds from ALL available deals (unfiltered) ───────────────
_all_scores = [vfm_from_mapping(d, weights) for d in all_available if _nan_safe(d.get("price"), 0) > 0]
p75 = float(np.percentile(_all_scores, 75)) if _all_scores else 350.0
p50 = float(np.percentile(_all_scores, 50)) if _all_scores else 250.0

if not deals:
    st.title("Mac 好價雷達")
    st.warning("找不到符合條件的物件。請調整篩選條件後重試。")
    st.stop()

df = pd.DataFrame(deals)
if "source" not in df.columns:
    df["source"] = ""
df["source"] = df["source"].fillna("")

# Client-side source filter (both/none selected)
if len(_selected_sources) == 0:
    st.title("Mac 好價雷達")
    st.warning("請至少選擇一個賣場來源（PTT 或 蝦皮）。")
    st.stop()

# ── Recalculate VFM with user weights ─────────────────────────────────────────
df["vfm_score"] = df.apply(lambda r: vfm_from_mapping(r.to_dict(), weights), axis=1)
df = df.sort_values("vfm_score", ascending=False).reset_index(drop=True)

# ── Header ─────────────────────────────────────────────────────────────────────
# The sources, derived rather than written out, so a fourth scraper cannot be
# left off this line the way Carousell was left off the other two.
st.markdown(f'<div class="st-eyebrow">二手 · {escape(" · ".join(SOURCE_LABELS[s] for s in SOURCES))} · 每日更新</div>',
            unsafe_allow_html=True)
st.title("Mac 好價雷達")
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

# ── 分數色帶圖例 ─────────────────────────────────────────────────────────────
# Squares in the verdict colours, not emoji. The cut points come from every
# available listing rather than from the filtered view, so narrowing to four
# machines does not move the standard they are judged against. Stated on the
# page because a band with an unexplained basis is a band nobody trusts.
st.markdown(f"""
<div class="vfm-legend">
  <span style="color:var(--ink-soft);font-weight:500;">全站基準</span>
  <span style="display:flex;gap:7px;align-items:center;"><b style="background:var(--good);"></b><i>划算</i> ≥ {p75:.0f}</span>
  <span style="display:flex;gap:7px;align-items:center;"><b style="background:var(--mid);"></b><i>普通</i> ≥ {p50:.0f}</span>
  <span style="display:flex;gap:7px;align-items:center;"><b style="background:var(--low);"></b><i>偏貴</i> &lt; {p50:.0f}</span>
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
        # Every figure here comes from score_engine, so the breakdown shown to
        # the reader cannot disagree with the score above it.
        top = df.iloc[0].to_dict()
        chip_t = str(top.get("chip") or "")
        base_t = get_benchmark(chip_t)
        year_t = int(_nan_safe(top.get("release_year"), current_year()))
        age_t  = max(0, current_year() - year_t)
        depr_t = round(depreciation(year_t), 4)
        r_t    = ram_mult if _nan_safe(top.get("ram_gb"), 0) >= RAM_BONUS_THRESHOLD_GB else 1.0
        s_t    = ssd_mult if _nan_safe(top.get("ssd_gb"), 0) >= SSD_BONUS_THRESHOLD_GB else 1.0
        form_t = weights.form_weight(form_factor_key(top.get("series"), top.get("screen_size")))
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
        return "var(--good)", "#2E7D5B"
    if score >= p50:
        return "var(--mid)", "#A8761F"
    return "var(--low)", "#9E4F55"


def _model_label(row: dict) -> str:
    """What the listing is, as the pipeline understands it.

    Seller titles bury the model inside shop names, coupon slogans and stock
    codes — 『澄橘』Macbook Air 15 2025 M4 10C10G/16G/256G 午夜 瑕疵機《二手》A87078
    is one real example. This is the line the reader scans; the seller's own
    wording stays underneath as the way to check it.

    Every part is optional, because any of them can be missing from a parse,
    and a label reading "MacBook · None" would be worse than a short one.
    """
    series = str(row.get("series") or "").lower()
    family = "Air" if "air" in series else "Pro" if "pro" in series else ""

    parts = [" ".join(p for p in ("MacBook", family) if p)]
    # Apple's marketing size, not the measured diagonal. Sellers copy 13, 13.3
    # and 13.6 for the same machine, and one listing claimed 15.6 — a size
    # Apple has never made. nominal_inches derives it from the same function
    # that picks the scoring multiplier, so the two cannot drift.
    if (inches := nominal_inches(row.get("series"), row.get("screen_size"))):
        parts[0] += f' {inches}"'
    if (chip := str(row.get("chip") or "").strip()) and chip.lower() != "none":
        parts.append(chip)
    return " · ".join(parts)


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
    # A defect warning outranks "最划算": the top-scoring listing was a 瑕疵機.
    if (defects := defects_for(row)):
        badge = (f'<span class="badge badge--warn" title="{escape(" / ".join(defects), quote=True)}">'
                 f'瑕疵</span>') + badge

    return (
        f'<a class="deal{" deal--top" if is_top else ""}" href="{escape(url, quote=True)}"'
        f' target="_blank" rel="noopener" title="{escape(title, quote=True)}"'
        f' style="--tone:{tone};--tone-glow:{glow}">'
        f'<div class="deal__score">'
        f'<div class="deal__box"><span>{score:.0f}</span></div>'
        f'</div>'
        f'<div class="deal__body">'
        f'<div class="deal__model">{badge}{escape(_model_label(row))}</div>'
        f'<div class="deal__title">{escape(title)}</div>'
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
# The unit is stated once here instead of on every row, and the median gives
# the number a reference point: 750 means nothing until you know the middle of
# the set is 626.
_caption = (
    '<div class="deal-caption">'
    '<span>CP 值 — 效能分 / 每千元</span>'
    # "全站", not just "中位數": these come from every available listing, not
    # from the rows currently shown. Filtering to four items must not move the
    # standard a listing is judged against — that was the objection to
    # percentile ranking in decisions.md #6, and the label has to say so.
    f'<span>全站中位數 {p50:.0f}</span>'
    '</div>'
)
st.markdown(f'<div class="deal-list">{_caption}{"".join(deals)}</div>',
            unsafe_allow_html=True)

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
    f"<div style='text-align:center;padding:4px 0;color:var(--ink-faint);"
    f"font-size:12px;'>共 {len(df)} 筆</div>",
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
_REPO_URL = "https://github.com/SaintY-918/mac-valuer"

_link_html = (
    f'<a href="{escape(_SAINTECH_URL, quote=True)}" target="_blank" rel="noopener">'
    f'SainTech 頻道{_ARROW_SVG}</a>'
    if _SAINTECH_URL else ""
) + (
    f'<a href="{_REPO_URL}" target="_blank" rel="noopener">'
    f'原始碼 GitHub{_ARROW_SVG}</a>'
)
st.markdown(
    f'<div class="st-footer">'
    f'<div>資料來源　{escape(" · ".join(SOURCE_LABELS[s] for s in SOURCES))}</div>'
    f'{_link_html}'
    f'</div>',
    unsafe_allow_html=True,
)
