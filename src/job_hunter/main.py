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
from job_hunter.mailer import send_jobs_email
from job_hunter.notifier_discord import send_discord_notification

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config.json"))
PENDING_JOBS_PATH = Path(os.environ.get("PENDING_JOBS_PATH", "pending_jobs.json"))

# Environment variable overrides — used in GitHub Actions (secrets)
ENV_OVERRIDES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "minimax_api_key": "MINIMAX_API_KEY",
    "minimax_model": "MINIMAX_MODEL",
    "email_sender": "EMAIL_SENDER",
    "email_app_password": "EMAIL_APP_PASSWORD",
    "email_recipient": "EMAIL_RECIPIENT",
    "discord_webhook_url": "DISCORD_WEBHOOK_URL",
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
        choices=["email", "discord"],
        default="email",
        help="Notification channel (default: email)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    resume_tag = " (resume)" if args.resume else ""
    logger.info(
        "=== Job Hunter pipeline starting [provider: %s%s] ===",
        args.provider,
        resume_tag,
    )

    config = load_config()

    # Step 0: AI Profile Analysis & Refinement
    logger.info("--- Step 0: AI Profile Analysis ---")
    from job_hunter.filter import analyze_and_refine_profile
    config = analyze_and_refine_profile(config, provider=args.provider)
    if config.get("search_tips"):
        logger.info(f"Search tips: {config['search_tips']}")
    logger.info(f"Refined profile: {config.get('profile', '')[:100]}...")
    logger.info(f"Keywords: {config.get('keywords', [])}")

    # Validate provider-specific API keys
    if args.provider == "anthropic" and not config.get("anthropic_api_key"):
        logger.error("Missing anthropic_api_key — set it in .env or ANTHROPIC_API_KEY env var")
        sys.exit(1)

    if args.provider == "minimax" and not config.get("minimax_api_key"):
        logger.error("Missing minimax_api_key — set it in .env or MINIMAX_API_KEY env var")
        sys.exit(1)

    if args.notify == "discord" and not config.get("discord_webhook_url"):
        logger.error("Missing discord_webhook_url — set it in .env or DISCORD_WEBHOOK_URL env var")
        sys.exit(1)

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

    # Step 3: Send notification
    logger.info(
        "--- Step 3: Sending %s notification (%d approved jobs) ---",
        args.notify,
        len(approved_jobs),
    )
    try:
        if args.notify == "discord":
            send_discord_notification(approved_jobs, config)
        else:
            send_jobs_email(approved_jobs, config)
    except RuntimeError:
        logger.error("Notification failed. Run with --resume to retry without re-collecting.")
        sys.exit(1)

    logger.info(
        "=== Pipeline complete: %d jobs sent via %s ===",
        len(approved_jobs),
        args.notify,
    )


if __name__ == "__main__":
    main()
