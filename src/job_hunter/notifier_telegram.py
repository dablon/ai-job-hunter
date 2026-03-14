"""notifier_telegram.py — Sends approved job listings to a Telegram chat via Bot API."""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096


def send_telegram_notification(jobs: list[dict], config: dict) -> None:
    """Send approved jobs to Telegram via Bot API.

    Required config keys:
    - telegram_bot_token: Bot token from @BotFather
    - telegram_chat_id: Chat/group/channel ID to send to

    Raises RuntimeError if sending fails.
    """
    token = config["telegram_bot_token"]
    chat_id = config["telegram_chat_id"]
    date_str = datetime.now().strftime("%d/%m/%Y")

    messages = _build_messages(jobs, date_str)

    for i, message in enumerate(messages):
        url = TELEGRAM_API.format(token=token)
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")
        except Exception as exc:
            logger.exception("Telegram send failed (message %d/%d)", i + 1, len(messages))
            raise RuntimeError(f"Telegram notification failed: {exc}") from exc

    logger.info("Telegram notification sent (%d jobs in %d messages)", len(jobs), len(messages))


def _build_messages(jobs: list[dict], date_str: str) -> list[str]:
    """Build Telegram messages, splitting if too long."""
    header = (
        f"<b>Job Hunter</b> — {len(jobs)} job{'s' if len(jobs) != 1 else ''} "
        f"found ({date_str})\n\n"
    )

    job_blocks = []
    for i, job in enumerate(jobs, 1):
        block = _format_job(i, job)
        job_blocks.append(block)

    # Pack jobs into messages respecting MAX_MESSAGE_LENGTH
    messages = []
    current = header
    for block in job_blocks:
        if len(current) + len(block) > MAX_MESSAGE_LENGTH:
            messages.append(current)
            current = ""
        current += block

    if current.strip():
        messages.append(current)

    return messages


def _format_job(index: int, job: dict) -> str:
    """Format a single job for Telegram HTML."""
    title = _esc(job.get("title", "No title"))
    company = _esc(job.get("company", "Unknown"))
    location = _esc(job.get("location", ""))
    url = job.get("url", "")
    salary = _esc(job.get("salary", ""))
    reason = _esc(job.get("match_reason", ""))
    source = job.get("source", "").capitalize()

    lines = [f"<b>{index}.</b> <a href=\"{url}\">{title}</a>"]
    lines.append(f"    <b>{company}</b> — {location}")
    if salary:
        lines.append(f"    💰 {salary}")
    lines.append(f"    📌 {source}")
    if reason:
        lines.append(f"    <i>{reason[:300]}</i>")
    lines.append("")

    return "\n".join(lines) + "\n"


def _esc(text: str) -> str:
    """Escape HTML special chars for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def validate_telegram_config(config: dict) -> bool:
    """Check if Telegram config keys exist."""
    for key in ("telegram_bot_token", "telegram_chat_id"):
        if not config.get(key):
            logger.warning("Missing Telegram config: %s — telegram will be skipped", key)
            return False
    return True
