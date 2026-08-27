from typing import Dict

# Apple Silicon benchmark scores — Geekbench 6 multi-core.
#
# One source for the whole table, otherwise the numbers are not comparable and
# neither are the VFM scores derived from them. Spot-checked against published
# results: M1 8,313 / M2 9,465 / M3 11,649 / M3 Max 20,785, all within a few
# percent of the values here.
#
# Sources:
#   https://browser.geekbench.com/mac-benchmarks
#   M5      17,933  https://browser.geekbench.com/macs/macbook-pro-14-inch-2025
#   M5 Pro  28,436  https://www.notebookcheck.net/Apple-M5-Pro-18-Core-Processor-Benchmarks-and-Specs.1242671.0.html
#   M5 Max  29,233  https://www.macrumors.com/2026/03/05/m5-max-geekbench-benchmarks/
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
    # Measured, not extrapolated. The generational deltas line up with Apple's
    # own claims: M4 Pro -> M5 Pro is +29% against their "up to 30% multithreaded",
    # and M4 Max -> M5 Max is +12% against their "14-15% faster".
    "M5": 17933,
    "M5 Pro": 28436,
    "M5 Max": 29233,
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
