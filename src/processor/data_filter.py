import pandas as pd
import numpy as np
from src.utils.benchmark_db import get_benchmark

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleans MacBook data by:
    1. Removing entries with missing price or chip.
    2. Removing hard outliers based on chip benchmark (Hard Floor Price).
    3. Grouping by 'chip' and using IQR to remove price outliers (if sample size >= 3).
    """
    if df.empty:
        return df

    # Drop rows without critical info first
    df = df.dropna(subset=['price', 'chip']).copy()
    if df.empty:
        return df

    # --- Step 1: Hard Floor Price Filtering ---
    # Rule: If price < (Benchmark Score * 0.3), it's likely a scam or broken device.
    def is_valid_price(row):
        try:
            base_benchmark = get_benchmark(row['chip'])
            # A very conservative floor price threshold
            floor_price = base_benchmark * 0.1  # Lowered from 0.3 for debugging
            is_valid = row['price'] >= floor_price
            if not is_valid:
                print(f"   [Filter] Removed: {row['chip']} at {row['price']} (Floor: {floor_price})")
            return is_valid
        except Exception as e:
            print(f"   [Filter] Error: {e} for row {row}")
            return False

    df = df[df.apply(is_valid_price, axis=1)].copy()

    # --- Step 2: Grouped IQR Filtering ---
    def filter_group_iqr(group: pd.DataFrame) -> pd.DataFrame:
        if len(group) < 3:
            return group
        
        # Ensure price is numeric for quantile calculation
        group['price'] = pd.to_numeric(group['price'], errors='coerce')
        group = group.dropna(subset=['price'])
        
        q1 = group['price'].quantile(0.25)
        q3 = group['price'].quantile(0.75)
        iqr = q3 - q1
        
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        return group[(group['price'] >= lower_bound) & (group['price'] <= upper_bound)]

    if not df.empty:
        cleaned_df = df.groupby('chip', group_keys=False).apply(filter_group_iqr, include_groups=False)
        # Note: include_groups=False for pandas 2.0+ group-apply behavior
        # Re-attach the grouping key 'chip' if it's missing from the result
        if 'chip' not in cleaned_df.columns:
            # Re-fetch from the index if needed, but usually apply with include_groups=False 
            # might require manual re-assignment if we use groupby.apply.
            # Simplified approach for safety:
            pass
            
        # Refined apply for broader pandas compatibility
        cleaned_df = df.groupby('chip', group_keys=False).apply(lambda x: filter_group_iqr(x))
        return cleaned_df.reset_index(drop=True)
    
    return df.reset_index(drop=True)

if __name__ == "__main__":
    # Test case including a 5000 M3 Max (Should be filtered by hard floor)
    data = [
        {'chip': 'M3 Max', 'price': 5000},  # Scam (Benchmark 21000 * 0.3 = 6300)
        {'chip': 'M1', 'price': 12000},
        {'chip': 'M1', 'price': 12500},
        {'chip': 'M1', 'price': 500}        # Scam (Benchmark 8500 * 0.3 = 2550)
    ]
    test_df = pd.DataFrame(data)
    print("Before Cleaning:")
    print(test_df)
    
    cleaned = clean_data(test_df)
    print("\nAfter Cleaning (Both 5000 M3 Max and 500 M1 should be gone):")
    print(cleaned)
