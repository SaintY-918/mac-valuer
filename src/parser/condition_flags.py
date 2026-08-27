"""Detect listings with a functional defect.

The VFM formula rewards a low price, and a broken machine is cheap because it is
broken — so defective units float to the top. Measured on live data, the two
highest-scoring listings were a 瑕疵機 and an 外接機 (dead screen), the first
carrying the "最划算" badge with nothing on the card to say otherwise.

Flagging is deliberately narrow. Only faults that affect whether the machine
works count; cosmetic wear is normal on a used machine and warning about it
would be noise that trains the reader to ignore the badge.
"""

import re

# Faults that stop the machine being fully usable.
_DEFECT_TERMS = [
    "瑕疵", "故障", "損壞", "破損", "破裂", "碎裂",
    "無法開機", "不開機", "無法充電", "無法密合", "無法使用",
    "泡水", "進水", "受潮",
    "電池膨脹", "膨脹",
    "零件機", "報廢", "當零件",
    "外接機",          # sold as a desktop because the built-in screen is dead
    "螢幕壞", "面板壞", "鍵盤壞",
]

# Sellers grade stock A/B/C. C is where functional faults live — the live data
# had "C 級 | 上蓋無法密合" and "C 級 | 螢幕右下邊框破裂". B is cosmetic.
_GRADE_C_RE = re.compile(r"\bC\s*級")

# "外觀無傷無碰撞" and "完全無撞無傷" describe a clean machine while containing
# the words a naive scan looks for. A negation immediately before the term
# inverts it.
_NEGATIONS = ("無", "沒有", "沒", "不", "免", "未")


def _negated(text: str, index: int) -> bool:
    """True when the term at `index` is preceded by a negation."""
    return any(
        text[max(0, index - len(neg)):index] == neg
        for neg in _NEGATIONS
    )


def find_defects(*parts) -> list[str]:
    """Return the defect terms present across title, condition and body.

    Callers pass raw DataFrame cells, so a missing value arrives as NaN — which
    is a float and, unlike None, is truthy. Coerce everything and drop what is
    not real text rather than trusting the caller to clean it.
    """
    text = " ".join(
        str(p) for p in parts
        if isinstance(p, str) and p.strip()
    )
    found = []
    for term in _DEFECT_TERMS:
        start = 0
        while (i := text.find(term, start)) != -1:
            if not _negated(text, i):
                found.append(term)
                break
            start = i + len(term)
    if _GRADE_C_RE.search(text):
        found.append("C級")
    return found


def has_defect(*parts: str | None) -> bool:
    return bool(find_defects(*parts))
