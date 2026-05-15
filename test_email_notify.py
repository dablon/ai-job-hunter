"""Quick email test — no pipeline, just verify credentials work."""
import sys
sys.path.insert(0, "src")

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv(usecwd=True))

import os
from job_hunter.mailer import send_jobs_email, validate_smtp_config

# Load config same way main.py does
config = {
    "email_sender": os.environ.get("EMAIL_SENDER", ""),
    "email_app_password": os.environ.get("EMAIL_APP_PASSWORD", ""),
    "email_recipient": os.environ.get("EMAIL_RECIPIENT", ""),
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.sendgrid.net"),
    "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
    "smtp_user": os.environ.get("SMTP_USER", "apikey"),
    "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
    "sendgrid_api_key": os.environ.get("SENDGRID_API_KEY", ""),
}

print(f"Sender: {config['email_sender']}")
print(f"Recipient: {config['email_recipient']}")
print(f"SendGrid API key set: {'Yes' if config['sendgrid_api_key'] else 'No'}")
print(f"SMTP host: {config['smtp_host']}:{config['smtp_port']}")

# Validate first
print("\nValidating config...")
if validate_smtp_config(config):
    print("Config validation: OK")
else:
    print("Config validation: FAILED")
    sys.exit(1)

# Send a minimal test job list (1 job, no prep guide)
test_jobs = [{
    "title": "Test Job — Email Notification Working",
    "company": "Job Hunter Test",
    "location": "Remote",
    "url": "https://example.com/test",
    "source": "test",
    "match_reason": "Testing email delivery",
}]

print("\nSending test email...")
try:
    send_jobs_email(test_jobs, config)
    print("SUCCESS: Email sent!")
except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)