"""mailer.py — Builds and sends an HTML email with approved job listings."""

import html
import logging
import smtplib
import socket
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from job_hunter.researcher import format_preparation_guide_html, format_preparation_guide_for_display

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# SendGrid HTTP API (preferred over SMTP — uses port 443, more reliable)
SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


# Source color mapping
SOURCE_COLORS = {
    "linkedin": "#0a66c2",
    "indeed": "#2164f3",
    "glassdoor": "#0caa41",
    "gupy": "#ff6b35",
    "remoteok": "#0dafd4",
    "weworkremotely": "#007bff",
}


def source_color_hex(source: str) -> str:
    """Return a hex color for the given job source."""
    return SOURCE_COLORS.get(source.lower(), "#666666")


def validate_smtp_config(config: dict) -> bool:
    """Validate email configuration early, before the pipeline runs.

    Prefers SendGrid HTTP API (port 443) over SMTP (port 587) when available.
    Returns True if valid, False otherwise (logs warnings).
    """
    for key in ("email_sender", "email_recipient"):
        if not config.get(key):
            logger.warning("Missing email config: %s — email will be skipped", key)
            return False

    # Check SendGrid HTTP API first (more reliable — uses port 443)
    sendgrid_key = (
        config.get("sendgrid_api_key")
        or (config.get("smtp_password", "") if "SG." in config.get("smtp_password", "") else "")
    )
    if sendgrid_key:
        # Test SendGrid API with a lightweight POST (zero-cost, no actual mail sent)
        try:
            resp = requests.post(
                "https://api.sendgrid.com/v3/api_keys/validates",
                headers={"Authorization": f"Bearer {sendgrid_key}", "Content-Type": "application/json"},
                json={},
                timeout=10,
            )
            # 2xx = valid key & reachable; any 4xx (not 429) also means the key is valid
            if 200 <= resp.status_code < 300 or resp.status_code in (400, 401, 403, 404):
                logger.info("SendGrid API reachable — HTTP API will be used")
                return True
            logger.warning("SendGrid API returned %s — will fall back to SMTP", resp.status_code)
        except Exception as exc:
            logger.warning("SendGrid API unreachable: %s — will fall back to SMTP", exc)

    # Fall back to SMTP
    smtp_host = config.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(config.get("smtp_port", "587"))

    if not config.get("email_app_password"):
        logger.warning("Missing email_app_password — email will be skipped")
        return False

    try:
        sock = socket.create_connection((smtp_host, smtp_port), timeout=10)
        sock.close()
        logger.info("SMTP reachable: %s:%d", smtp_host, smtp_port)
        return True
    except OSError as exc:
        logger.warning(
            "SMTP server unreachable (%s:%d): %s — email will be skipped",
            smtp_host, smtp_port, exc,
        )
        return False


def send_jobs_email(jobs: list[dict], config: dict) -> None:
    """Build an HTML email from approved jobs and send via SendGrid HTTP API or SMTP.

    Raises RuntimeError if sending fails.
    """
    sender = config["email_sender"]
    recipient = config["email_recipient"]

    date_str = datetime.now().strftime("%d/%m/%Y")
    subject = f"Job Hunter — {len(jobs)} jobs found ({date_str})"
    provider = config.get("provider", "minimax")
    html_body = _build_html(jobs, provider)
    text_body = _build_plaintext(jobs, provider)

    # Try SendGrid HTTP API first — use dedicated key or detect from smtp_password
    sendgrid_key = (
        config.get("sendgrid_api_key")
        or (config.get("smtp_password", "") if config.get("smtp_password", "").startswith("SG.") else "")
    )
    if sendgrid_key:
        try:
            _send_via_sendgrid_api(sender, recipient, subject, html_body, text_body, sendgrid_key)
            logger.info("Email sent to %s via SendGrid HTTP API (%d jobs)", recipient, len(jobs))
            return
        except RuntimeError:
            logger.warning("SendGrid HTTP API failed — falling back to SMTP")

    # Fall back to SMTP
    password = config["email_app_password"]
    smtp_host = config.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(config.get("smtp_port", "587"))
    smtp_user = config.get("smtp_user", sender)
    smtp_password = config.get("smtp_password", "")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                server.login(smtp_user, smtp_password or password)
                server.sendmail(sender, [recipient], msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_password or password)
                server.sendmail(sender, [recipient], msg.as_string())
        logger.info("Email sent to %s (%d jobs)", recipient, len(jobs))
    except Exception as exc:
        logger.exception("Failed to send email")
        raise RuntimeError(f"Email send failed: {exc}") from exc


def _send_via_sendgrid_api(
    sender: str,
    recipient: str,
    subject: str,
    html_body: str,
    text_body: str,
    api_key: str,
) -> None:
    """Send email via SendGrid HTTP API (port 443 — no SMTP timeout issues)."""
    # Parse sender name and email
    sender_name, sender_email = _parse_sender(sender)
    recipient_name, recipient_email = _parse_sender(recipient)

    payload = {
        "personalizations": [
            {
                "to": [{"email": recipient_email, "name": recipient_name}] if recipient_name else [{"email": recipient_email}],
                "subject": subject,
            }
        ],
        "from": {"email": sender_email, "name": sender_name} if sender_name else {"email": sender_email},
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": html_body},
        ],
    }

    try:
        resp = requests.post(
            SENDGRID_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"SendGrid API returned {resp.status_code}: {resp.text[:200]}")
    except requests.RequestException as exc:
        raise RuntimeError(f"SendGrid HTTP API request failed: {exc}") from exc


def _parse_sender(sender: str) -> tuple[str | None, str]:
    """Parse 'Name <email@example.com>' into (name, email) parts."""
    if "<" in sender and ">" in sender:
        name = sender.split("<")[0].strip().strip('"')
        email = sender.split("<")[1].strip(">").strip()
        return (name or None, email)
    return (None, sender.strip())


def _safe_str(value) -> str:
    """Safely convert any value to string."""
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN check
            return ""
        return str(int(value)) if value == int(value) else str(value)
    return str(value)


def _build_html(jobs: list[dict], provider: str = "minimax") -> str:
    """Build the HTML email body."""
    date_str = datetime.now().strftime("%d/%m/%Y")
    esc = html.escape
    provider_label = provider.capitalize()

    cards = []
    for job in jobs:
        source = _safe_str(job.get("source", "")).lower()
        color = source_color_hex(source)
        source_label = esc(source.capitalize())
        title = esc(_safe_str(job.get("title", "No title")))
        company = esc(_safe_str(job.get("company", "Unknown company")))
        location = esc(_safe_str(job.get("location", "")))
        url = esc(_safe_str(job.get("url", "#")), quote=True)
        reason = esc(_safe_str(job.get("match_reason", "")))
        salary = esc(_safe_str(job.get("salary", "")))
        date_posted = job.get("date_posted", "")
        if date_posted and len(date_posted) >= 10:
            date_posted = esc(date_posted[:10])

        salary_html = ""
        if salary:
            # Use text fallback for emoji compatibility with all email clients
            salary_html = (
                f'<p style="margin:4px 0;font-size:13px;color:#1a8917;font-weight:bold;">'
                f'<span role="img" aria-label="salary">💰</span> {salary}</p>'
            )

        # Preparation guide section (if available)
        prep_guide_html = ""
        prep_guide = job.get("preparation_guide")
        if prep_guide:
            prep_guide_html = format_preparation_guide_html(prep_guide)

        card = f"""
        <div style="border:1px solid #e0e0e0;border-radius:8px;padding:16px;margin-bottom:16px;background:#ffffff;">
          <h3 style="margin:0 0 6px 0;font-size:16px;">
            <a href="{url}" style="color:#0066cc;text-decoration:none;">
              {title}
            </a>
          </h3>
          <p style="margin:4px 0;color:#333;font-size:14px;">
            <strong>{company}</strong>
            &nbsp;&mdash;&nbsp;{location}
          </p>
          {salary_html}
          <p style="margin:4px 0;font-size:12px;color:#888;">
            <span style="background:{color};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{source_label}</span>
            &nbsp; Posted: {date_posted or 'N/A'}
          </p>
          {'' if not reason else f'<p style="margin:10px 0 0 0;padding:8px 10px;background:#f0f7ff;border-left:3px solid #0066cc;border-radius:0 4px 4px 0;font-size:13px;color:#333;"><strong>Reason:</strong> {reason}</p>'}
          {prep_guide_html}
        </div>"""
        cards.append(card)

    cards_html = "\n".join(cards)
    job_count = len(jobs)
    plural = "s" if job_count != 1 else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:680px;margin:24px auto;padding:0 16px;">
    <div style="background:#ffffff;border-radius:8px;padding:24px 28px;margin-bottom:16px;border-bottom:3px solid #0066cc;">
      <h1 style="margin:0;font-size:22px;color:#1a1a1a;">Job Hunter</h1>
      <p style="margin:6px 0 0 0;color:#666;font-size:14px;">{date_str} &mdash; {job_count} relevant job{plural} found</p>
    </div>
    {cards_html}
    <p style="font-size:11px;color:#aaa;text-align:center;margin-top:24px;padding-bottom:16px;">
      Automatically generated by Job Hunter ({provider_label} Edition).
    </p>
  </div>
</body>
</html>"""


def _build_plaintext(jobs: list[dict], provider: str = "minimax") -> str:
    """Build a plain-text fallback."""
    date_str = datetime.now().strftime("%d/%m/%Y")
    provider_label = provider.capitalize()
    lines = [
        f"Job Hunter — {date_str}",
        f"{len(jobs)} job(s) found",
        "=" * 50,
        "",
    ]
    for i, job in enumerate(jobs, 1):
        company = _safe_str(job.get("company", "Unknown"))
        lines += [
            f"{i}. {_safe_str(job.get('title', ''))} @ {company}",
            f"   Location: {_safe_str(job.get('location', ''))}",
        ]
        if job.get("salary"):
            lines.append(f"   Salary: {_safe_str(job['salary'])}")
        lines.append(f"   Link: {_safe_str(job.get('url', ''))}")
        if job.get("match_reason"):
            lines.append(f"   Reason: {_safe_str(job['match_reason'])}")
        # Add preparation guide if available
        prep_guide = job.get("preparation_guide")
        if prep_guide:
            prep_text = format_preparation_guide_for_display(prep_guide)
            if prep_text:
                lines.append("   --- Preparation Guide ---")
                for pline in prep_text.split("\n"):
                    lines.append(f"   {pline}")
                lines.append("")
        lines.append("")
    lines.append(f"Automatically generated by Job Hunter ({provider_label} Edition).")
    return "\n".join(lines)
