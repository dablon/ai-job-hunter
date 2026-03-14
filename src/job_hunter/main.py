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

# ANSI colors for terminal output
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    LIGHT_GREEN = "\033[92m"
    LIGHT_RED = "\033[91m"
    LIGHT_YELLOW = "\033[93m"
    ORANGE = "\033[38;5;208m"
    PURPLE = "\033[38;5;129m"
    PINK = "\033[38;5;205m"


# ASCII Art and UI Elements
BANNER = f"""
{Colors.CYAN}
    ██████╗ ███████╗ ██████╗ ██╗   ██╗██╗     ███████╗███████╗
    ██╔══██╗██╔════╝██╔════╝ ██║   ██║██║     ██╔════╝██╔════╝
    ██████╔╝█████╗   ██║  ███╗██║   ██║██║     █████╗  ███████╗
    ██╔══██╗██╔══╝   ██║   ██║██║   ██║██║     ██╔══╝  ╚════██║
    ██║  ██║███████╗ ╚██████╔╝╚██████╔╝███████╗███████╗███████║
    ╚═╝  ╚═╝╚══════╝  ╚═════╝  ╚═════╝ ╚══════╝╚══════╝╚══════╝
    {Colors.MAGENTA}╔═══════════════════════════════════════════════════════════╗
    ║         🤖  AI-POWERED JOB HUNTING PIPELINE v2.0            ║
    ╚═══════════════════════════════════════════════════════════╝{Colors.RESET}
"""

# Step icons with ASCII boxes
STEP_ICONS = {
    "collect": f"{Colors.CYAN}🔍{Colors.RESET}",
    "filter": f"{Colors.MAGENTA}🧠{Colors.RESET}",
    "notify": f"{Colors.GREEN}📨{Colors.RESET}",
    "profile": f"{Colors.YELLOW}👤{Colors.RESET}",
}

STEP_TITLES = {
    "collect": "GATHERING JOB LISTINGS",
    "filter": "AI-POWERED FILTERING",
    "notify": "SENDING NOTIFICATIONS",
    "profile": "PROFILE ANALYSIS",
}


def colorize(text: str, color: str) -> str:
    """Add color to text if terminal supports it."""
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{Colors.RESET}"


def color_job_count(count: int) -> str:
    """Colorize job count based on number."""
    if count == 0:
        return colorize(str(count), Colors.GRAY)
    elif count < 5:
        return colorize(str(count), Colors.YELLOW)
    elif count < 15:
        return colorize(str(count), Colors.GREEN)
    else:
        return colorize(str(count), Colors.CYAN + Colors.BOLD)


def box_print(content: str, color: str = Colors.BLUE, width: int = 58) -> None:
    """Print content inside an ASCII box."""
    lines = content.split('\n')
    print(colorize("┌" + "─" * width + "┐", color))
    for line in lines:
        padding = width - len(line)
        print(colorize("│", color) + " " + line + " " * max(0, padding - 1) + colorize("│", color))
    print(colorize("└" + "─" * width + "┘", color))


def step_box(step_name: str, info: str, status: str = "running") -> None:
    """Print a step in a styled box."""
    icon = STEP_ICONS.get(step_name, "▸")
    title = STEP_TITLES.get(step_name, step_name.upper())

    status_colors = {
        "running": Colors.CYAN,
        "done": Colors.GREEN,
        "warn": Colors.YELLOW,
        "error": Colors.RED,
    }
    status_icon = {
        "running": "◐",
        "done": "●",
        "warn": "⚠",
        "error": "✖",
    }

    c = status_colors.get(status, Colors.GRAY)
    s = status_icon.get(status, "○")

    print()
    print(colorize("╭" + "─" * 56 + "╮", c))
    print(colorize(f"│ {icon} {title:^50} {s} │", c))
    print(colorize("├" + "─" * 56 + "┤", c))
    for line in info.split('\n'):
        print(colorize("│ ", c) + line.ljust(56) + colorize(" │", c))
    print(colorize("╰" + "─" * 56 + "╯", c))


def progress_bar(current: int, total: int, prefix: str = "", width: int = 30) -> str:
    """Generate a simple progress bar string."""
    if total == 0:
        return colorize(f"{prefix} [{' ' * width}]", Colors.GRAY)
    percent = min(current / total, 1.0)
    filled = int(width * percent)
    bar = "█" * filled + "░" * (width - filled)
    return colorize(f"{prefix} [{bar}] {int(percent * 100)}%", Colors.CYAN)


def print_stats(label: str, value: str, color: str = Colors.GRAY) -> None:
    """Print a stat line with label and value."""
    print(f"  {colorize('▸', color)} {label}: {value}")


def print_divider(char: str = "─", color: str = Colors.DIM) -> None:
    """Print a divider line."""
    print(colorize(char * 58, color))


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels."""

    COLORS = {
        "DEBUG": Colors.GRAY,
        "INFO": Colors.BLUE,
        "WARNING": Colors.YELLOW,
        "ERROR": Colors.RED,
        "CRITICAL": Colors.RED + Colors.BOLD,
    }

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, Colors.RESET)
        record.levelname = f"{log_color}{record.levelname}{Colors.RESET}"
        return super().format(record)


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "config.json"))
PENDING_JOBS_PATH = Path(os.environ.get("PENDING_JOBS_PATH", "pending_jobs.json"))
SENT_URLS_PATH = Path(os.environ.get("SENT_URLS_PATH", "sent_urls.json"))
MAX_KEYWORDS = 20

# Default reports directory - configurable via environment variable
DEFAULT_REPORT_DIR = Path("/app/data/reports")

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
    try:
        with open(PENDING_JOBS_PATH, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
        logger.info("Checkpoint saved: %d jobs in %s", len(jobs), PENDING_JOBS_PATH)
    except OSError as e:
        logger.error("Failed to save pending jobs: %s", e)


def _load_pending() -> list[dict]:
    if not PENDING_JOBS_PATH.exists():
        logger.warning("No pending jobs file found")
        return []

    try:
        with open(PENDING_JOBS_PATH, encoding="utf-8") as f:
            jobs = json.load(f)
        logger.info("Checkpoint loaded: %d jobs from %s", len(jobs), PENDING_JOBS_PATH)
        return jobs
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load pending jobs (starting fresh): %s", e)
        return []


def _save_report(jobs: list[dict], provider: str = "minimax") -> Path:
    from datetime import datetime
    from job_hunter.mailer import _build_html, _build_plaintext

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Allow override via REPORT_DIR env var, with fallback for Docker vs local
    report_dir_str = os.environ.get("REPORT_DIR", "")
    if report_dir_str:
        report_dir = Path(report_dir_str)
    else:
        # Check which report directory is available and writable
        # Priority: Docker default > local ./reports > config/reports
        if DEFAULT_REPORT_DIR.exists() and os.access(DEFAULT_REPORT_DIR, os.W_OK):
            report_dir = DEFAULT_REPORT_DIR
        else:
            # Fall back to local reports directory
            report_dir = Path("config/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    html_content = _build_html(jobs, provider)
    txt_content = _build_plaintext(jobs, provider)

    html_path = report_dir / f"jobs_{date_str}.html"
    txt_path = report_dir / f"jobs_{date_str}.txt"

    html_path.write_text(html_content, encoding="utf-8")
    txt_path.write_text(txt_content, encoding="utf-8")

    logger.info("Report saved: %s and %s", html_path, txt_path)
    return html_path


def _load_sent_urls() -> set[str]:
    """Load previously sent job URLs for deduplication across runs."""
    if not SENT_URLS_PATH.exists():
        return set()

    try:
        with open(SENT_URLS_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load sent URLs (starting fresh): %s", e)
        return set()


def _save_sent_urls(urls: set[str]) -> None:
    """Persist sent job URLs."""
    try:
        with open(SENT_URLS_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(urls), f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error("Failed to save sent URLs: %s", e)


def _deduplicate_jobs(jobs: list[dict], sent_urls: set[str]) -> list[dict]:
    """Remove jobs that were already sent in previous runs."""
    new_jobs = [j for j in jobs if j.get("url", "") not in sent_urls]
    removed = len(jobs) - len(new_jobs)
    if removed:
        logger.info("Deduplication: removed %d already-sent jobs", removed)
    return new_jobs


def _parse_notify_channels(raw: str, config: dict) -> list[str]:
    """Parse comma-separated notify channels, validating each.

    Also automatically adds discord as fallback if discord_webhook_url is configured.
    """
    channels = [c.strip().lower() for c in raw.split(",") if c.strip()]
    invalid = [c for c in channels if c not in VALID_CHANNELS]
    if invalid:
        logger.error("Invalid notify channels: %s. Valid: %s", invalid, sorted(VALID_CHANNELS))
        sys.exit(1)

    # Default to email if nothing specified
    if not channels:
        channels = ["email"]

    # Auto-add discord as fallback if webhook URL is configured and not already specified
    if "discord" not in channels and config.get("discord_webhook_url"):
        channels.append("discord")
        logger.info("Auto-added Discord as notification fallback")

    return channels


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

    # Setup colored logging
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    ))
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.INFO)

    # Load config first so we can check for auto-fallback channels
    config = load_config()
    channels = _parse_notify_channels(args.notify, config)

    # Print ASCII banner
    print(BANNER)

    # Config info box
    config_lines = [
        f"Provider: {colorize(args.provider.upper(), Colors.GREEN)}",
        f"Notify:   {colorize(', '.join(channels), Colors.YELLOW)}",
    ]
    if args.resume:
        config_lines.append(f"Mode:     {colorize('RESUME', Colors.MAGENTA)}")

    box_print("\n".join(config_lines), Colors.BLUE)
    print()

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
        keywords = config.get("keywords", [])
        step_box("collect", f"Searching {len(keywords)} keywords\nSources: LinkedIn, Indeed, Glassdoor, Gupy, RemoteOK", "running")

        jobs = collect_all(config)

        if not jobs:
            step_box("collect", "No jobs found from any source", "warn")
            return

        # Show collection summary with color
        step_box("collect", f"Successfully collected {color_job_count(len(jobs))} jobs", "done")

        _save_pending(jobs)

    # Deduplicate against previously sent jobs
    sent_urls = _load_sent_urls()
    jobs = _deduplicate_jobs(jobs, sent_urls)

    if not jobs:
        step_box("collect", "All collected jobs were already sent in previous runs", "warn")
        return

    # Step 2: AI filtering
    filter_info = f"Processing {len(jobs)} jobs\nAI Provider: {colorize(args.provider.upper(), Colors.GREEN)}"
    step_box("filter", filter_info, "running")

    try:
        approved_jobs = filter_jobs(jobs, config, provider=args.provider)
    except RuntimeError:
        step_box("filter", "AI filtering failed. Run with --resume to retry.", "error")
        sys.exit(1)

    if not approved_jobs:
        step_box("filter", "No jobs passed the AI filter", "warn")
        return

    # Show filtering results
    pass_rate = (len(approved_jobs)/len(jobs)*100) if jobs else 0
    filter_result = f"Approved {color_job_count(len(approved_jobs))} jobs\nPass rate: {colorize(f'{pass_rate:.1f}%', Colors.GREEN)}"
    step_box("filter", filter_result, "done")

    # Step 3: Send notifications
    ch_str = colorize(", ".join(viable_channels), Colors.YELLOW) if viable_channels else colorize("none", Colors.GRAY)
    notify_info = f"Channels: {ch_str}\nJobs to send: {color_job_count(len(approved_jobs))}"
    step_box("notify", notify_info, "running")

    report_path = _save_report(approved_jobs, args.provider)
    succeeded = _send_notifications(viable_channels, approved_jobs, config, report_path)

    # Track sent URLs for deduplication
    for job in approved_jobs:
        url = job.get("url", "")
        if url:
            sent_urls.add(url)
    _save_sent_urls(sent_urls)

    # Final completion with fancy ASCII
    print()
    print(colorize("╔" + "═" * 56 + "╗", Colors.GREEN))
    print(colorize("║", Colors.GREEN) + colorize(" 🎉 HUNT COMPLETE! ".center(56), Colors.GREEN + Colors.BOLD) + colorize("║", Colors.GREEN))
    print(colorize("╠" + "═" * 56 + "╣", Colors.GREEN))
    print(colorize("║", Colors.GREEN))
    print(colorize("║", Colors.GREEN) + f"   {colorize(' Jobs Found:', Colors.GRAY)}     {colorize(f'{len(approved_jobs)}', Colors.CYAN + Colors.BOLD)}".ljust(57) + colorize("║", Colors.GREEN))
    notify_str = colorize(", ".join(succeeded), Colors.YELLOW) if succeeded else colorize("report only", Colors.GRAY)
    print(colorize("║", Colors.GREEN) + f"   {colorize(' Notified Via:', Colors.GRAY)}    {notify_str}".ljust(57) + colorize("║", Colors.GREEN))
    print(colorize("║", Colors.GREEN) + f"   {colorize(' Total Sent:', Colors.GRAY)}      {colorize(f'{len(sent_urls)}', Colors.GREEN)}".ljust(57) + colorize("║", Colors.GREEN))
    print(colorize("║", Colors.GREEN))
    print(colorize("╚" + "═" * 56 + "╝", Colors.GREEN))
    print()

    # Easter egg
    if len(approved_jobs) >= 10:
        print(colorize("   🎊 Amazing haul! You're on fire! 🔥", Colors.YELLOW))
    elif len(approved_jobs) >= 5:
        print(colorize("   👍 Solid results! Keep it up!", Colors.GREEN))
    elif len(approved_jobs) > 0:
        print(colorize("   💪 Every step counts. Tomorrow brings new opportunities!", Colors.BLUE))
    else:
        print(colorize("   💤 No matches this time. The perfect job is just around the corner!", Colors.GRAY))
    print()


if __name__ == "__main__":
    main()
