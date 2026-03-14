"""main.py — Pipeline orchestrator for the job-hunter system (Minimax Edition)."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from job_hunter.collector import collect_all
from job_hunter.filter import filter_jobs
from job_hunter.mailer import send_jobs_email, validate_smtp_config
from job_hunter.notifier_discord import send_discord_notification
from job_hunter.notifier_telegram import send_telegram_notification, validate_telegram_config
from job_hunter.notifier_twilio import (
    send_sms_notification,
    send_whatsapp_notification,
    validate_twilio_config,
)

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config.json"))
PENDING_JOBS_PATH = Path(os.environ.get("PENDING_JOBS_PATH", "pending_jobs.json"))
SENT_URLS_PATH = Path(os.environ.get("SENT_URLS_PATH", "sent_urls.json"))
MAX_KEYWORDS = 12

# Environment variable overrides — used in GitHub Actions (secrets)
VALID_CHANNELS = {"email", "discord", "telegram", "sms", "whatsapp"}

ENV_OVERRIDES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "minimax_api_key": "MINIMAX_API_KEY",
    "minimax_model": "MINIMAX_MODEL",
    "email_sender": "EMAIL_SENDER",
    "email_app_password": "EMAIL_APP_PASSWORD",
    "email_recipient": "EMAIL_RECIPIENT",
    "discord_webhook_url": "DISCORD_WEBHOOK_URL",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "twilio_account_sid": "TWILIO_ACCOUNT_SID",
    "twilio_auth_token": "TWILIO_AUTH_TOKEN",
    "twilio_from_number": "TWILIO_FROM_NUMBER",
    "twilio_to_number": "TWILIO_TO_NUMBER",
    "twilio_whatsapp_from": "TWILIO_WHATSAPP_FROM",
    "twilio_whatsapp_to": "TWILIO_WHATSAPP_TO",
}


def load_config() -> dict:
    """Load .env and config.json, then overlay environment variable overrides.

    Environment variables take precedence over config.json values.
    """
    load_dotenv(find_dotenv(usecwd=True))
    config: dict = {}

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = json.load(f)
    else:
        logger.warning("config.json not found — relying entirely on environment variables")

    for config_key, env_var in ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value:
            config[config_key] = value
            if "api_key" in config_key:
                logger.info(f"Loaded {config_key}: {value[:10]}...")

    if os.environ.get("PROFILE"):
        config["profile"] = os.environ["PROFILE"]

    if os.environ.get("KEYWORDS"):
        config["keywords"] = [k.strip() for k in os.environ["KEYWORDS"].split(",")]

    return config


def _save_pending(jobs: list[dict]) -> None:
    with open(PENDING_JOBS_PATH, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    logger.info("Checkpoint saved: %d jobs in %s", len(jobs), PENDING_JOBS_PATH)


def _load_pending() -> list[dict]:
    with open(PENDING_JOBS_PATH, encoding="utf-8") as f:
        jobs = json.load(f)
    logger.info("Checkpoint loaded: %d jobs from %s", len(jobs), PENDING_JOBS_PATH)
    return jobs


def _save_report(jobs: list[dict]) -> Path:
    from datetime import datetime
    from job_hunter.mailer import _build_html, _build_plaintext

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path("/app/data/reports")
    report_dir.mkdir(exist_ok=True)

    html_content = _build_html(jobs)
    txt_content = _build_plaintext(jobs)

    html_path = report_dir / f"jobs_{date_str}.html"
    txt_path = report_dir / f"jobs_{date_str}.txt"

    html_path.write_text(html_content, encoding="utf-8")
    txt_path.write_text(txt_content, encoding="utf-8")

    logger.info("Report saved: %s and %s", html_path, txt_path)
    return html_path


def _load_sent_urls() -> set[str]:
    """Load previously sent job URLs for deduplication across runs."""
    if SENT_URLS_PATH.exists():
        with open(SENT_URLS_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_sent_urls(urls: set[str]) -> None:
    """Persist sent job URLs."""
    with open(SENT_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=2)


def _deduplicate_jobs(jobs: list[dict], sent_urls: set[str]) -> list[dict]:
    """Remove jobs that were already sent in previous runs."""
    new_jobs = [j for j in jobs if j.get("url", "") not in sent_urls]
    removed = len(jobs) - len(new_jobs)
    if removed:
        logger.info("Deduplication: removed %d already-sent jobs", removed)
    return new_jobs


def _parse_notify_channels(raw: str) -> list[str]:
    """Parse comma-separated notify channels, validating each."""
    channels = [c.strip().lower() for c in raw.split(",") if c.strip()]
    invalid = [c for c in channels if c not in VALID_CHANNELS]
    if invalid:
        logger.error("Invalid notify channels: %s. Valid: %s", invalid, sorted(VALID_CHANNELS))
        sys.exit(1)
    return channels or ["email"]


def _validate_channels(channels: list[str], config: dict) -> list[str]:
    """Validate each channel config upfront and return only the viable ones."""
    viable = []
    for ch in channels:
        if ch == "email":
            if validate_smtp_config(config):
                viable.append(ch)
        elif ch == "discord":
            if config.get("discord_webhook_url"):
                viable.append(ch)
            else:
                logger.warning("Missing discord_webhook_url — discord will be skipped")
        elif ch == "telegram":
            if validate_telegram_config(config):
                viable.append(ch)
        elif ch == "sms":
            if validate_twilio_config(config, "sms"):
                viable.append(ch)
        elif ch == "whatsapp":
            if validate_twilio_config(config, "whatsapp"):
                viable.append(ch)
    return viable


def _send_notifications(
    channels: list[str], approved_jobs: list[dict], config: dict, report_path: Path
) -> list[str]:
    """Send notifications to all viable channels. Returns list of channels that succeeded."""
    dispatchers = {
        "email": send_jobs_email,
        "discord": send_discord_notification,
        "telegram": send_telegram_notification,
        "sms": send_sms_notification,
        "whatsapp": send_whatsapp_notification,
    }
    succeeded = []
    for ch in channels:
        try:
            dispatchers[ch](approved_jobs, config)
            succeeded.append(ch)
        except RuntimeError as e:
            logger.warning("%s notification failed: %s", ch, e)

    if not succeeded:
        logger.warning("All notification channels failed. Report saved to %s", report_path)

    return succeeded


def main() -> None:
    parser = argparse.ArgumentParser(description="Job Hunter pipeline (AI Edition)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=f"Skip collection and load jobs from {PENDING_JOBS_PATH}",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "opencode", "minimax"],
        default="minimax",
        help="AI provider for filtering (default: minimax)",
    )
    parser.add_argument(
        "--notify",
        default="email",
        help="Notification channels, comma-separated (email,discord,telegram,sms,whatsapp)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    channels = _parse_notify_channels(args.notify)

    resume_tag = " (resume)" if args.resume else ""
    logger.info(
        "=== Job Hunter pipeline starting [provider: %s, notify: %s%s] ===",
        args.provider,
        ",".join(channels),
        resume_tag,
    )

    config = load_config()

    # --- Early validation (fail fast, not after 17 min) ---
    if args.provider == "anthropic" and not config.get("anthropic_api_key"):
        logger.error("Missing anthropic_api_key — set it in .env or ANTHROPIC_API_KEY env var")
        sys.exit(1)

    if args.provider == "minimax" and not config.get("minimax_api_key"):
        logger.error("Missing minimax_api_key — set it in .env or MINIMAX_API_KEY env var")
        sys.exit(1)

    viable_channels = _validate_channels(channels, config)
    if not viable_channels:
        logger.warning(
            "No notification channels are configured. Pipeline will run but only save reports."
        )

    # Step 0: AI Profile Analysis & Refinement
    logger.info("--- Step 0: AI Profile Analysis ---")
    from job_hunter.filter import analyze_and_refine_profile
    original_profile = config.get("profile", "")
    config = analyze_and_refine_profile(config, provider=args.provider)
    # Keep the original detailed profile for filtering — only use refined keywords
    config["profile"] = original_profile
    if config.get("search_tips"):
        logger.info("Search tips: %s", config["search_tips"])
    logger.info("Profile: %s...", original_profile[:100])

    # Cap keywords to avoid excessive scraping time
    keywords = config.get("keywords", [])
    if len(keywords) > MAX_KEYWORDS:
        logger.info(
            "Capping keywords from %d to %d", len(keywords), MAX_KEYWORDS
        )
        config["keywords"] = keywords[:MAX_KEYWORDS]
    logger.info("Keywords (%d): %s", len(config.get("keywords", [])), config.get("keywords", []))

    if args.resume:
        if not PENDING_JOBS_PATH.exists():
            logger.error(
                "--resume requested but %s not found. Run without --resume first.",
                PENDING_JOBS_PATH,
            )
            sys.exit(1)
        jobs = _load_pending()
    else:
        if not config.get("keywords"):
            logger.error("Missing keywords — set at least one search keyword in config.json")
            sys.exit(1)

        # Step 1: Collect jobs from all sources
        logger.info("--- Step 1: Collecting jobs ---")
        jobs = collect_all(config)

        if not jobs:
            logger.info("No jobs collected from any source. Exiting.")
            return

        _save_pending(jobs)

    # Deduplicate against previously sent jobs
    sent_urls = _load_sent_urls()
    jobs = _deduplicate_jobs(jobs, sent_urls)

    if not jobs:
        logger.info("All collected jobs were already sent in previous runs. Exiting.")
        return

    # Step 2: AI filtering
    logger.info("--- Step 2: AI filtering (%d jobs) [%s] ---", len(jobs), args.provider)
    try:
        approved_jobs = filter_jobs(jobs, config, provider=args.provider)
    except RuntimeError:
        logger.error("AI filtering failed. Run with --resume to retry without re-collecting.")
        sys.exit(1)

    if not approved_jobs:
        logger.info("No jobs passed AI filter.")
        return

    # Step 3: Send notifications
    logger.info(
        "--- Step 3: Sending notifications [%s] (%d approved jobs) ---",
        ",".join(viable_channels) or "none",
        len(approved_jobs),
    )

    report_path = _save_report(approved_jobs)
    succeeded = _send_notifications(viable_channels, approved_jobs, config, report_path)

    # Track sent URLs for deduplication
    for job in approved_jobs:
        url = job.get("url", "")
        if url:
            sent_urls.add(url)
    _save_sent_urls(sent_urls)

    logger.info(
        "=== Pipeline complete: %d jobs, notified via: %s ===",
        len(approved_jobs),
        ",".join(succeeded) if succeeded else "report only",
    )


if __name__ == "__main__":
    main()
