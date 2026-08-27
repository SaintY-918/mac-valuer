from typing import Dict

# Common Apple Silicon Benchmark Scores (e.g., Geekbench 6 Multi-core approximate)
CHIP_BENCHMARKS: Dict[str, int] = {
    "M1": 8500,
    "M1 Pro": 12000,
    "M1 Max": 12500,
    "M1 Ultra": 24000,
    "M2": 10000,
    "M2 Pro": 14000,
    "M2 Max": 14500,
    "M2 Ultra": 28000,
    "M3": 11500,
    "M3 Pro": 15500,
    "M3 Max": 21000,
    "M4": 14500,
    "M4 Pro": 22000,
    "M4 Max": 26000,
    # M5 figures are EXTRAPOLATED from the generation-on-generation trend above
    # (~+17%), not measured. M5 machines were being dropped entirely for want of
    # an entry, and a rough score beats discarding the newest listings — but
    # replace these with real Geekbench numbers when convenient.
    "M5": 17000,
    "M5 Pro": 25000,
    "M5 Max": 30000,
}

def get_benchmark(chip: str) -> int:
    """Returns the benchmark score for a given chip. Defaults to 5000 if not found."""
    # Normalize chip name (case-insensitive and strip)
    normalized_chip = chip.strip()
    # Try direct match or fuzzy match (case-insensitive)
    for key, value in CHIP_BENCHMARKS.items():
        if key.lower() == normalized_chip.lower():
            return value
    return 5000  # Default score for older or unknown chips
