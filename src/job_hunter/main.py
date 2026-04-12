"""main.py — Pipeline orchestrator for the job-hunter system (Minimax Edition)."""

import argparse
import json
import logging
import os
import signal
import sys

# Fix UTF-8 encoding for Windows console
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from job_hunter.collector import collect_all
from job_hunter.filter import filter_jobs
from job_hunter.mailer import send_jobs_email, validate_smtp_config
from job_hunter.researcher import research_jobs
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
    "research": f"{Colors.YELLOW}📚{Colors.RESET}",
    "notify": f"{Colors.GREEN}📨{Colors.RESET}",
    "profile": f"{Colors.YELLOW}👤{Colors.RESET}",
}

STEP_TITLES = {
    "collect": "GATHERING JOB LISTINGS",
    "filter": "AI-POWERED FILTERING",
    "research": "JOB PREPARATION RESEARCH",
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
    "sendgrid_api_key": "SENDGRID_API_KEY",
    "jooble_api_key": "JOOBLE_API_KEY",
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

    # Write latest symlink for easy access to most recent report
    try:
        latest_html = report_dir / "latest.html"
        latest_txt = report_dir / "latest.txt"
        # Use relative path so it works inside and outside containers
        latest_html.unlink(missing_ok=True)
        latest_txt.unlink(missing_ok=True)
        latest_html.symlink_to(html_path.name)
        latest_txt.symlink_to(txt_path.name)
    except OSError as exc:
        logger.warning("Could not create latest symlink: %s", exc)

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


def _filter_by_salary(jobs: list[dict], config: dict) -> list[dict]:
    """Filter jobs by salary range (normalized to USD/year)."""
    from job_hunter.collector import normalize_salary

    min_salary = config.get("salary_min_usd", 0)
    max_salary = config.get("salary_max_usd", float("inf"))

    if min_salary == 0 and max_salary == float("inf"):
        return jobs  # No filtering needed

    filtered = []
    for job in jobs:
        salary_str = job.get("salary", "")
        if not salary_str:
            # Include jobs without salary (user can decide)
            filtered.append(job)
            continue

        normalized = normalize_salary(salary_str)
        if not normalized:
            # Include jobs with unparseable salary
            filtered.append(job)
            continue

        job_min = normalized.get("min_usd", 0)
        job_max = normalized.get("max_usd", 0)

        # Job must overlap with desired range
        if job_max > 0 and job_min < min_salary:
            continue  # Job pays too little
        if job_min > max_salary:
            continue  # Job pays too much (actually, this is fine - include it)

        filtered.append(job)

    removed = len(jobs) - len(filtered)
    if removed:
        logger.info("Salary filter: removed %d jobs outside range %d-%d USD/year",
                   removed, min_salary, max_salary)

    return filtered


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

    # Silent fallback: if discord webhook URL is configured, add it as a fallback
    # even if not explicitly requested — this ensures at least one channel works
    if "discord" not in channels and config.get("discord_webhook_url"):
        channels.append("discord")
        logger.info("Discord auto-added as fallback (webhook configured)")

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
    channels: list[str], approved_jobs: list[dict], config: dict, report_path: Path, dry_run: bool = False
) -> list[str]:
    """Send notifications to all viable channels. Returns list of channels that succeeded."""
    if dry_run:
        logger.info("[DRY RUN] Would send notifications to: %s", ", ".join(channels) or "none")
        logger.info("[DRY RUN] Jobs that would be sent: %d", len(approved_jobs))
        return []

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


def run_health_checks(config: dict) -> bool:
    """Run health checks on configured APIs and services.

    Returns True if all checks pass, False otherwise.
    """
    import requests

    print()
    print(colorize("╭────────────────────────────────────────────────────────╮", Colors.CYAN))
    print(colorize("│ 🔧              HEALTH CHECKS                      ◐ │", Colors.CYAN))
    print(colorize("╰────────────────────────────────────────────────────────╯", Colors.CYAN))

    all_passed = True

    # Check Minimax API
    if config.get("minimax_api_key"):
        try:
            url = "https://api.minimax.io/v1/text/chatcompletion_v2"
            headers = {
                "Authorization": f"Bearer {config.get('minimax_api_key')}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": config.get("minimax_model", "MiniMax-M2.5"),
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 1,
            }
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code in (200, 400):  # 400 is ok - just testing auth
                print(colorize("  ✓ Minimax API: OK", Colors.GREEN))
            else:
                print(colorize(f"  ✗ Minimax API: HTTP {response.status_code}", Colors.RED))
                all_passed = False
        except Exception as e:
            print(colorize(f"  ✗ Minimax API: {e}", Colors.RED))
            all_passed = False
    else:
        print(colorize("  ⊘ Minimax API: Not configured", Colors.YELLOW))

    # Check Anthropic API
    if config.get("anthropic_api_key"):
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=config.get("anthropic_api_key"))
            # Just test auth - don't actually send a message
            print(colorize("  ✓ Anthropic API: OK", Colors.GREEN))
        except Exception as e:
            print(colorize(f"  ✗ Anthropic API: {e}", Colors.RED))
            all_passed = False
    else:
        print(colorize("  ⊘ Anthropic API: Not configured", Colors.YELLOW))

    # Check SMTP (email)
    if config.get("email_sender") and config.get("email_app_password"):
        print(colorize("  ✓ Email: Configured", Colors.GREEN))
    else:
        print(colorize("  ⊘ Email: Not configured", Colors.YELLOW))

    # Check Discord webhook
    if config.get("discord_webhook_url"):
        print(colorize("  ✓ Discord: Configured", Colors.GREEN))
    else:
        print(colorize("  ⊘ Discord: Not configured", Colors.YELLOW))

    # Check Telegram
    if config.get("telegram_bot_token") and config.get("telegram_chat_id"):
        print(colorize("  ✓ Telegram: Configured", Colors.GREEN))
    else:
        print(colorize("  ⊘ Telegram: Not configured", Colors.YELLOW))

    # Check Twilio
    if config.get("twilio_account_sid") and config.get("twilio_auth_token"):
        print(colorize("  ✓ Twilio: Configured", Colors.GREEN))
    else:
        print(colorize("  ⊘ Twilio: Not configured", Colors.YELLOW))

    # Check Jooble API
    if config.get("jooble_api_key"):
        print(colorize("  ✓ Jooble: Configured", Colors.GREEN))
    else:
        print(colorize("  ⊘ Jooble: Not configured (optional)", Colors.GRAY))

    # Summary
    if all_passed:
        print(colorize("\n  ✓ All configured services are healthy!", Colors.GREEN))
    else:
        print(colorize("\n  ✗ Some services have issues - check configuration", Colors.RED))

    print()
    return all_passed


def main() -> None:
    # Graceful shutdown: save pending jobs on SIGTERM/SIGINT
    # Using mutable containers so the nested handler can read/write without nonlocal issues
    _handler_state = {"jobs": [], "config": {}, "shutdown_requested": False}

    def _sigterm_handler(signum, frame):
        if _handler_state["shutdown_requested"]:
            return  # Already handling — don't double-process
        _handler_state["shutdown_requested"] = True
        logger.warning("Shutdown signal received — saving pending jobs before exit...")
        jobs = _handler_state["jobs"]
        cfg = _handler_state["config"]
        if jobs and cfg:
            try:
                from job_hunter.mailer import _build_html, _build_plaintext
                report_path = _save_report(jobs, cfg.get("provider", "minimax"))
                _save_pending(jobs)
                logger.info("Shutdown save complete. Report: %s", report_path)
            except Exception:
                logger.exception("Failed to save pending jobs on shutdown")
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline but skip sending notifications",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run health checks and exit",
    )
    parser.add_argument(
        "--analyze-linkedin",
        type=str,
        metavar="URL",
        help="Analyze a LinkedIn profile URL and generate config.json",
    )
    parser.add_argument(
        "--profile-text",
        type=str,
        metavar="TEXT",
        help="Profile text to use if LinkedIn scraping fails (alternative to --analyze-linkedin)",
    )
    parser.add_argument(
        "--output-config",
        type=str,
        default="config.json",
        help="Output path for generated config (default: config.json)",
    )
    parser.add_argument(
        "--no-research",
        action="store_true",
        help="Skip AI research step (saves API credits, no preparation guides generated)",
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

    # Run health checks if requested
    if args.health_check:
        run_health_checks(config)
        return

    # Analyze LinkedIn profile if requested
    if args.analyze_linkedin:
        from job_hunter.linkedin_analyzer import analyze_linkedin_profile, save_config

        print(BANNER)
        step_box("profile", f"Analyzing LinkedIn profile\n{args.analyze_linkedin}", "running")

        # Validate provider
        if args.provider == "anthropic" and not config.get("anthropic_api_key"):
            logger.error("Missing anthropic_api_key for profile analysis")
            sys.exit(1)
        if args.provider == "minimax" and not config.get("minimax_api_key"):
            logger.error("Missing minimax_api_key for profile analysis")
            sys.exit(1)

        try:
            # Handle --profile-text option
            profile_text = args.profile_text

            # If only --profile-text is provided without URL, create a dummy URL
            profile_url = args.analyze_linkedin
            if not profile_url and profile_text:
                profile_url = "https://linkedin.com/in/manual-input"

            generated_config = analyze_linkedin_profile(
                url=profile_url,
                config=config,
                provider=args.provider,
                profile_text=profile_text
            )

            # Save config
            output_path = Path(args.output_config)
            saved_path = save_config(generated_config, output_path)

            step_box(
                "profile",
                f"Config generated successfully!\nSaved to: {saved_path}\n"
                f"Keywords: {len(generated_config.get('keywords', []))}\n"
                f"Locations: {', '.join(generated_config.get('locations', []))}",
                "done"
            )
            return

        except Exception as e:
            step_box("profile", f"Analysis failed: {e}", "error")
            sys.exit(1)

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
    if args.dry_run:
        config_lines.append(f"Mode:     {colorize('DRY RUN', Colors.ORANGE)}")

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
        _handler_state["jobs"] = jobs
        _handler_state["config"] = config
    else:
        if not config.get("keywords"):
            logger.error("Missing keywords — set at least one search keyword in config.json")
            sys.exit(1)

        # Step 1: Collect jobs from all sources
        keywords = config.get("keywords", [])
        step_box("collect", f"Searching {len(keywords)} keywords\nSources: LinkedIn, Indeed, Glassdoor, Gupy, RemoteOK", "running")

        jobs = collect_all(config)

        # Keep jobs in scope for SIGTERM handler
        _handler_state["jobs"] = jobs
        _handler_state["config"] = config

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

    # Filter by salary range
    jobs = _filter_by_salary(jobs, config)

    if not jobs:
        step_box("collect", "All jobs filtered out by salary range", "warn")
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

    # Step 3: AI Research — generate preparation guides for each approved job
    if approved_jobs and not args.no_research:
        research_info = f"Researching {len(approved_jobs)} jobs\nProvider: {colorize(args.provider.upper(), Colors.GREEN)}"
        step_box("research", research_info, "running")

        try:
            approved_jobs = research_jobs(approved_jobs, config, provider=args.provider, parallel=True)
            step_box("research", f"Preparation guides generated for {color_job_count(len(approved_jobs))} jobs", "done")
        except Exception:
            logger.exception("Research step failed — continuing without preparation guides")
            step_box("research", "Research failed — jobs sent without preparation guides", "warn")
    elif args.no_research:
        step_box("research", "Research skipped by --no-research flag", "warn")

    # Step 4: Send notifications
    ch_str = colorize(", ".join(viable_channels), Colors.YELLOW) if viable_channels else colorize("none", Colors.GRAY)
    notify_info = f"Channels: {ch_str}\nJobs to send: {color_job_count(len(approved_jobs))}"
    step_box("notify", notify_info, "running")

    report_path = _save_report(approved_jobs, args.provider)
    succeeded = _send_notifications(viable_channels, approved_jobs, config, report_path, dry_run=args.dry_run)

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
