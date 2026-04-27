import logging
import os
from typing import Dict

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 5

_url_warning_emitted = False


def _format_message(deal: Dict) -> str:
    source = deal.get("source") or "?"
    title = deal.get("title") or "(無標題)"
    price = deal.get("price")
    score = deal.get("vfm_score")
    url = deal.get("url") or ""

    price_str = f"NT$ {int(price):,}" if price is not None else "NT$ ?"
    score_str = f"{float(score):.0f}" if score is not None else "?"

    return (
        "🔥 **撿漏警報**\n"
        f"平台：`{source}`\n"
        f"商品：**{title}**\n"
        f"價格：**{price_str}**\n"
        f"CP值：`{score_str}`\n"
        f"{url}"
    )


def send_alert(deal: Dict) -> bool:
    """Send a Discord webhook alert for a single deal.

    Returns True iff Discord responded with any 2xx (typically 204 No Content).
    Returns False on missing URL, network failure, timeout, or non-2xx response.
    Never raises.
    """
    global _url_warning_emitted
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        if not _url_warning_emitted:
            logger.warning("DISCORD_WEBHOOK_URL not set; notifier disabled (will not warn again).")
            _url_warning_emitted = True
        return False

    payload = {"content": _format_message(deal)}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        logger.warning("Discord webhook request failed for %s: %s", deal.get("url"), e)
        return False

    if 200 <= resp.status_code < 300:
        return True
    logger.warning(
        "Discord webhook non-2xx for %s: status=%d body=%s",
        deal.get("url"), resp.status_code, resp.text[:200],
    )
    return False
