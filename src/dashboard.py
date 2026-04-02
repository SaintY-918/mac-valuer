import streamlit as st
import pandas as pd
import os

# Set page config
st.set_page_config(page_title="saintech MacBook 智慧估價", layout="wide")

st.title("🍎 saintech 二手 MacBook 智慧估價系統")
st.markdown("---")

# Check if report exists
REPORT_PATH = "valuation_report.csv"

if not os.path.exists(REPORT_PATH):
    st.warning(f"⚠️ 找不到 {REPORT_PATH}。請先執行 `python src/main.py` 生成評估報告。")
else:
    # Load data
    df = pd.read_csv(REPORT_PATH)
    
    # Sidebar Filters
    st.sidebar.header("🔍 篩選條件")
    
    # Chip Filter
    all_chips = sorted(df['Chip'].unique().tolist())
    selected_chips = st.sidebar.multiselect("選擇晶片系列", all_chips, default=all_chips)
    
    # Price Filter
    df['Price_Num'] = df['Price'].str.replace(',', '').astype(int)
    min_p, max_p = int(df['Price_Num'].min()), int(df['Price_Num'].max())
    price_range = st.sidebar.slider("價格範圍", min_p, max_p, (min_p, max_p))

    # Apply Filters
    df_filtered = df[df['Chip'].isin(selected_chips)]
    df_filtered = df_filtered[(df_filtered['Price_Num'] >= price_range[0]) & (df_filtered['Price_Num'] <= price_range[1])]
    
    # 🏆 Best Deal Highlight
    if not df_filtered.empty:
        df_filtered = df_filtered.sort_values(by="VFM Score", ascending=False)
        best_deal = df_filtered.iloc[0]

        st.success(f"### 🏆 今日性價比之王")
        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
        with col1:
            st.metric("最推薦標題", best_deal['Title'])
        with col2:
            st.metric("晶片", best_deal['Chip'])
        with col3:
            st.metric("價格", f"{best_deal['Price']} 元")
        with col4:
            st.metric("性價比分數", f"{best_deal['VFM Score']} pts")
        
        st.markdown("---")

        # 📊 Data Table
        st.subheader("📋 完整估價列表")
        st.write("💡 點擊欄位標題可進行排序 | 🟢 系統推斷年份")
        
        # Format display dataframe
        display_df = df_filtered.copy()
        
        # Identification for inferred years
        if 'is_year_inferred' in display_df.columns:
            display_df['Year'] = display_df.apply(
                lambda x: f"{int(x['Year'])} 🟢" if x['is_year_inferred'] else f"{int(x['Year'])}", axis=1
            )
            display_df = display_df.drop(columns=['is_year_inferred'])

        # Display dataframe with specific columns
        cols_to_show = ["Title", "Chip", "RAM", "SSD", "Size", "Year", "Price", "VFM Score"]
        st.dataframe(
            display_df[cols_to_show],
            use_container_width=True,
            column_config={
                "VFM Score": st.column_config.NumberColumn(format="%.2f ⭐"),
                "Price": "價格 (TWD)",
                "Year": "上市年份",
                "Size": "螢幕尺寸"
            },
            hide_index=True
        )
    else:
        st.error("❌ 找不到符合篩選條件的機型。")

# Sidebar Footer
st.sidebar.markdown("---")
st.sidebar.info("💡 提示：🟢 代表年份為系統智慧推斷。")
st.sidebar.text("Data source: PTT MacShop")
