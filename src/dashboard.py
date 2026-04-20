import os

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="mac-valuer | 二手 MacBook 估價", layout="wide")
st.title("二手 MacBook 智慧估價系統")
st.markdown("---")

# --- Sidebar filters ---
st.sidebar.header("篩選條件")

chip_input = st.sidebar.text_input("晶片型號（模糊比對）", placeholder="例如：M3")
ram_options = [None, 8, 16, 18, 24, 32, 36, 48, 64]
ram_gb = st.sidebar.selectbox("RAM 大小", ram_options, format_func=lambda x: "不限" if x is None else f"{x} GB")
min_price = st.sidebar.number_input("最低價格（TWD）", min_value=0, value=0, step=1000)
max_price = st.sidebar.number_input("最高價格（TWD）", min_value=0, value=0, step=1000)
show_sold = st.sidebar.checkbox("顯示已售出物件", value=False)

# --- Fetch from API ---
params: dict = {"status": "sold" if show_sold else "available"}
if chip_input:
    params["chip"] = chip_input
if ram_gb:
    params["ram_gb"] = ram_gb
if min_price > 0:
    params["min_price"] = min_price
if max_price > 0:
    params["max_price"] = max_price

try:
    resp = requests.get(f"{API_BASE}/api/deals", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    deals = data.get("deals", [])
except requests.exceptions.ConnectionError:
    st.error(
        f"無法連線到 API 伺服器（{API_BASE}）。\n\n"
        "請先執行：`uvicorn api.main:app --reload --port 8000`"
    )
    st.stop()
except Exception as e:
    st.error(f"API 錯誤：{e}")
    st.stop()

if not deals:
    st.warning("找不到符合條件的物件。請調整篩選條件後重試。")
    st.stop()

df = pd.DataFrame(deals)

# --- Best deal highlight ---
best = df.iloc[0]
st.success("### 今日性價比之王")
c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
with c1:
    st.metric("標題", best.get("original_title", "")[:40])
with c2:
    st.metric("晶片", best.get("chip", "-"))
with c3:
    price_val = best.get("price")
    st.metric("價格", f"{int(price_val):,} 元" if price_val else "-")
with c4:
    vfm = best.get("vfm_score")
    st.metric("VFM 分數", f"{vfm} pts" if vfm else "-")

st.markdown("---")

# --- Data table ---
st.subheader(f"完整列表（共 {len(df)} 筆）")

display_cols = {
    "original_title": "標題",
    "chip": "晶片",
    "ram_gb": "RAM (GB)",
    "ssd_gb": "SSD (GB)",
    "screen_size": "螢幕吋",
    "release_year": "年份",
    "price": "價格 (TWD)",
    "location": "地區",
    "battery_health": "電池健康",
    "warranty_status": "保固",
    "condition": "成色",
    "vfm_score": "VFM 分數",
}

available = [c for c in display_cols if c in df.columns]
display_df = df[available].rename(columns=display_cols)

if "年份" in display_df.columns:
    display_df["年份"] = display_df["年份"].apply(
        lambda x: str(int(x)) if pd.notna(x) and x != 0 else "-"
    )

st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        "VFM 分數": st.column_config.NumberColumn(format="%.2f ⭐"),
        "價格 (TWD)": st.column_config.NumberColumn(format="%d"),
        "電池健康": st.column_config.NumberColumn(format="%d %%"),
    },
    hide_index=True,
)

st.sidebar.markdown("---")
st.sidebar.caption(f"資料來源：PTT MacShop　｜　API：{API_BASE}")
