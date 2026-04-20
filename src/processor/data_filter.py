import pandas as pd
import numpy as np
from src.utils.benchmark_db import get_benchmark

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleans MacBook data with robust chip name normalization and price filtering.
    """
    if df.empty:
        return df

    # 1. Drop rows without mandatory fields
    df = df.dropna(subset=['price', 'chip']).copy()
    if df.empty:
        return df

    # --- Step 1: Hard Floor Price Filtering ---
    def is_valid_price(row):
        try:
            chip_name = str(row['chip']).upper()
            # Normalize: "APPLE M1" -> "M1", "M1 MAX CHIP" -> "M1 MAX"
            if "APPLE " in chip_name: chip_name = chip_name.replace("APPLE ", "")
            chip_name = chip_name.split(" CHIP")[0]
            
            base_benchmark = get_benchmark(chip_name)
            # If benchmark found, use it. If not, use a very low global floor (5000)
            floor_price = base_benchmark * 0.1 if base_benchmark > 0 else 5000.0
            
            is_valid = float(row['price']) >= floor_price
            return is_valid
        except:
            return False

    df = df[df.apply(is_valid_price, axis=1)].copy()

    # --- Step 2: Grouped IQR Filtering (Optional skip for small datasets) ---
    def filter_group_iqr(group: pd.DataFrame) -> pd.DataFrame:
        if len(group) < 3:
            return group
        
        group['price'] = pd.to_numeric(group['price'], errors='coerce')
        group = group.dropna(subset=['price'])
        
        q1 = group['price'].quantile(0.25)
        q3 = group['price'].quantile(0.75)
        iqr = q3 - q1
        
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        return group[(group['price'] >= lower_bound) & (group['price'] <= upper_bound)]

    if not df.empty:
        # Simplified grouping apply for robustness
        cleaned_df = df.groupby('chip', group_keys=False).apply(lambda x: filter_group_iqr(x))
        return cleaned_df.reset_index(drop=True)
    
    return df.reset_index(drop=True)
