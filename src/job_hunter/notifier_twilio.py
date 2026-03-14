"""notifier_twilio.py — Sends approved job listings via Twilio SMS or WhatsApp."""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
SMS_MAX_CHARS = 1600  # Twilio concatenates into segments
WHATSAPP_MAX_CHARS = 4096


def send_sms_notification(jobs: list[dict], config: dict) -> None:
    """Send approved jobs via Twilio SMS.

    Required config keys:
    - twilio_account_sid
    - twilio_auth_token
    - twilio_from_number: Your Twilio phone number (e.g. +1234567890)
    - twilio_to_number: Recipient phone number (e.g. +57300...)

    Raises RuntimeError if sending fails.
    """
    _send_twilio(jobs, config, channel="sms")


def send_whatsapp_notification(jobs: list[dict], config: dict) -> None:
    """Send approved jobs via Twilio WhatsApp.

    Required config keys:
    - twilio_account_sid
    - twilio_auth_token
    - twilio_whatsapp_from: Your Twilio WhatsApp number (e.g. whatsapp:+14155238886)
    - twilio_whatsapp_to: Recipient WhatsApp number (e.g. whatsapp:+57300...)

    Raises RuntimeError if sending fails.
    """
    _send_twilio(jobs, config, channel="whatsapp")


def _send_twilio(jobs: list[dict], config: dict, channel: str) -> None:
    """Send messages via Twilio REST API (SMS or WhatsApp)."""
    sid = config["twilio_account_sid"]
    token = config["twilio_auth_token"]

    if channel == "whatsapp":
        from_number = config["twilio_whatsapp_from"]
        to_number = config["twilio_whatsapp_to"]
        max_chars = WHATSAPP_MAX_CHARS
    else:
        from_number = config["twilio_from_number"]
        to_number = config["twilio_to_number"]
        max_chars = SMS_MAX_CHARS

    messages = _build_messages(jobs, max_chars)
    url = TWILIO_API.format(sid=sid)

    for i, body in enumerate(messages):
        payload = {
            "From": from_number,
            "To": to_number,
            "Body": body,
        }

        try:
            resp = requests.post(
                url,
                data=payload,
                auth=(sid, token),
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.exception(
                "Twilio %s send failed (message %d/%d)", channel, i + 1, len(messages)
            )
            raise RuntimeError(f"Twilio {channel} failed: {exc}") from exc

    logger.info(
        "Twilio %s notification sent (%d jobs in %d messages)",
        channel, len(jobs), len(messages),
    )


def _build_messages(jobs: list[dict], max_chars: int) -> list[str]:
    """Build plain-text messages, splitting if needed."""
    date_str = datetime.now().strftime("%d/%m/%Y")
    header = f"Job Hunter — {len(jobs)} jobs ({date_str})\n\n"

    job_blocks = []
    for i, job in enumerate(jobs, 1):
        block = _format_job(i, job)
        job_blocks.append(block)

    messages = []
    current = header
    for block in job_blocks:
        if len(current) + len(block) > max_chars:
            messages.append(current.strip())
            current = ""
        current += block

    if current.strip():
        messages.append(current.strip())

    return messages


def _format_job(index: int, job: dict) -> str:
    """Format a single job for plain-text SMS/WhatsApp."""
    title = job.get("title", "No title")
    company = job.get("company", "Unknown")
    location = job.get("location", "")
    url = job.get("url", "")
    salary = job.get("salary", "")
    reason = job.get("match_reason", "")

    lines = [f"{index}. {title} @ {company}"]
    if location:
        lines.append(f"   {location}")
    if salary:
        lines.append(f"   💰 {salary}")
    if url:
        lines.append(f"   {url}")
    if reason:
        lines.append(f"   → {reason[:200]}")
    lines.append("")

    return "\n".join(lines) + "\n"


def validate_twilio_config(config: dict, channel: str) -> bool:
    """Check if Twilio config keys exist for the given channel."""
    required = ["twilio_account_sid", "twilio_auth_token"]
    if channel == "whatsapp":
        required += ["twilio_whatsapp_from", "twilio_whatsapp_to"]
    else:
        required += ["twilio_from_number", "twilio_to_number"]

    for key in required:
        if not config.get(key):
            logger.warning("Missing Twilio config: %s — %s will be skipped", key, channel)
            return False
    return True


