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

    # The alert calls this a bargain, and the VFM score rewards a low price —
    # so a machine that is cheap because it is broken scores highest of all.
    # The warning goes above the price, where it cannot be skimmed past.
    defects = deal.get("defects") or []
    warning = f"⚠️ **疑似瑕疵品**：{' / '.join(defects)}\n" if defects else ""

    return (
        "🔥 **撿漏警報**\n"
        f"{warning}"
        f"平台：`{source}`\n"
        f"商品：**{title}**\n"
        f"價格：**{price_str}**\n"
        f"CP值：`{score_str}`\n"
        f"{url}"
    )


_SOURCE_LABELS = {"ptt": "PTT", "shopee": "蝦皮", "carousell": "旋轉拍賣"}


def _label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)


def _heartbeat_content(stats: dict) -> str:
    """The daily summary text. Separate from sending so it can be tested."""
    counts: dict = stats.get("counts") or {}
    errors: dict = stats.get("errors") or {}
    alerts_sent = stats.get("alerts_sent", 0)

    # A run that died partway through is not a patrol summary with a bad number
    # in it — there is no summary, because the steps that produce one never ran.
    # Reporting "觸發警報：0 筆" here would be true and still misleading: it reads
    # as a quiet night rather than as a pipeline that stopped.
    fatal = stats.get("fatal")
    if fatal:
        reason = str(fatal).replace(chr(10), " ")[:180]
        lines = [f"- 原因：`{reason}`",
                 "- pipeline 在跑完之前中斷，本次**沒有完整結果**。"]
        for source in sorted(counts):
            lines.append(f"- {_label(source)}：中斷前已寫入 **{counts[source]}** 筆")
        return chr(10).join(["⛔ **每日爬蟲巡邏中止**", *lines])

    lines = []
    for source in sorted(set(counts) | set(errors)):
        if source in errors:
            reason = str(errors[source]).replace(chr(10), " ")[:180]
            lines.append(f"- ⛔ {_label(source)}：**爬取失敗** — `{reason}`")
        else:
            lines.append(f"- ✅ {_label(source)}：新增/更新 **{counts.get(source, 0)}** 筆")

    lines.append(f"- 觸發警報：**{alerts_sent}** 筆")

    # Reported on its own line, not as a scraper failure. Every source may have
    # been fetched perfectly and the listings still be unparsed, and calling
    # that "scrape failed" would point at the wrong thing entirely.
    quota = stats.get("quota_exhausted")
    if quota:
        lines.append("- 🪫 **Gemini 今日額度用盡**（免費方案 500 次／日）"
                     "，本次未解析的物件會在額度重置後補上")

    if errors:
        header = "⚠️ **每日爬蟲巡邏 — 有來源失敗**"
    elif quota:
        header = "🪫 **每日爬蟲巡邏完畢 — 解析未完成**"
    else:
        header = "✅ **每日爬蟲巡邏完畢**"
    return chr(10).join([header, *lines])


def send_heartbeat(stats: dict) -> None:
    """Send a daily pipeline summary to Discord. Silent no-op if webhook URL is unset.

    A source that errored is reported as ⛔ with its reason — never as "0 筆",
    which is indistinguishable from a genuinely quiet day and hid a completely
    broken Shopee scraper for weeks.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    content = _heartbeat_content(stats)
    try:
        resp = requests.post(webhook_url, json={"content": content}, timeout=HTTP_TIMEOUT_SECONDS)
        if not (200 <= resp.status_code < 300):
            logger.warning("Heartbeat non-2xx: status=%d", resp.status_code)
    except requests.RequestException as e:
        logger.warning("Heartbeat request failed: %s", e)


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
