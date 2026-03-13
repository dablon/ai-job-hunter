"""notifier_discord.py — Sends approved job listings to a Discord channel via webhook."""

import logging
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Source color mapping
SOURCE_COLORS = {
    "linkedin": 0x0A66C2,
    "indeed": 0x2164F3,
    "glassdoor": 0x0CAA41,
    "gupy": 0xFF6B35,
}
DEFAULT_SOURCE_COLOR = 0x666666

# Discord limits: 10 embeds per message, 2000 chars per embed description
MAX_EMBEDS_PER_MESSAGE = 10
EMBED_DESC_MAX_CHARS = 2000


def send_discord_notification(jobs: list[dict], config: dict) -> None:
    """Send approved jobs to Discord via webhook as embed messages.

    Raises RuntimeError if any POST request fails.
    """
    webhook_url = config["discord_webhook_url"]
    date_str = datetime.now().strftime("%d/%m/%Y")

    embeds = [_build_embed(job) for job in jobs]
    batches = [
        embeds[i : i + MAX_EMBEDS_PER_MESSAGE]
        for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE)
    ]

    for batch_idx, batch in enumerate(batches):
        payload: dict = {"embeds": batch}

        if batch_idx == 0:
            plural = "s" if len(jobs) != 1 else ""
            payload["content"] = (
                f"\U0001f50d **Job Hunter** — {len(jobs)} job{plural} "
                f"found ({date_str})"
            )

        _post_webhook(webhook_url, payload, batch_idx + 1, len(batches))

        if batch_idx < len(batches) - 1:
            time.sleep(1)

    logger.info(
        "Discord notification sent (%d jobs in %d messages)", len(jobs), len(batches)
    )


def _build_embed(job: dict) -> dict:
    """Build a single Discord embed for a job listing."""
    source = job.get("source", "").lower()
    color = SOURCE_COLORS.get(source, DEFAULT_SOURCE_COLOR)
    title = job.get("title", "No title")
    url = job.get("url", "")

    fields = [
        {"name": "Company", "value": job.get("company", "Unknown"), "inline": True},
        {"name": "Location", "value": job.get("location", "N/A"), "inline": True},
        {"name": "Source", "value": source.capitalize() or "N/A", "inline": True},
    ]

    reason = job.get("match_reason", "")
    if reason:
        if len(reason) > EMBED_DESC_MAX_CHARS:
            reason = reason[: EMBED_DESC_MAX_CHARS - 3] + "..."
        fields.append({"name": "Reason", "value": reason, "inline": False})

    embed: dict = {
        "title": title[:256],
        "color": color,
        "fields": fields,
    }

    if url:
        embed["url"] = url

    date_posted = job.get("date_posted", "")
    if date_posted:
        embed["footer"] = {"text": f"Posted: {date_posted[:10]}"}

    return embed


def _post_webhook(
    url: str, payload: dict, batch_num: int, total_batches: int
) -> None:
    """POST a payload to the Discord webhook, raising RuntimeError on failure."""
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.exception(
            "Discord webhook failed (batch %d/%d)", batch_num, total_batches
        )
        raise RuntimeError(f"Discord webhook failed: {exc}") from exc
