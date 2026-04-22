import re
from typing import Optional

# PTT posts use both ASCII [] and full-width ［］ brackets interchangeably
_OPEN = r'[\[［]'
_CLOSE = r'[\]］]'


def _section(body: str, *tags: str) -> Optional[str]:
    """Return stripped content of the first matching [TAG] section, up to the next tag."""
    for tag in tags:
        pat = rf'{_OPEN}\s*{re.escape(tag)}\s*{_CLOSE}\s*(.*?)(?={_OPEN}|$)'
        m = re.search(pat, body, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def extract_price(body: str) -> Optional[float]:
    sell_tag = chr(21806) + chr(20729)  # 售價
    content = _section(body, sell_tag)
    if not content:
        return None
    m = re.search(r"([0-9][0-9,]*)([kK])", content)
    k_match = bool(m)
    if not m:
        m = re.search(r"([0-9][0-9,]*)", content)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
        if k_match:
            val *= 1000
        return val if val >= 1000 else None
    except ValueError:
        return None

def extract_location(body: str) -> Optional[str]:
    content = _section(
        body,
        '交易方式/地點', '地點/交易方式',
        '交易方式', '交易地點', '地點',
    )
    if not content:
        return None
    first_line = content.split('\n')[0].strip()
    return first_line[:60] if first_line else None


def extract_warranty(body: str) -> Optional[str]:
    content = _section(body, '保固', '保固期限', '保固狀態')
    if not content:
        return None
    return content.split('\n')[0].strip()[:50]


def extract_spec_line(body: str) -> Optional[str]:
    """Return [規格] or [型號] section text for further RAM/SSD/screen_size parsing."""
    return _section(body, '規格', '機器規格', '配備', '型號')
