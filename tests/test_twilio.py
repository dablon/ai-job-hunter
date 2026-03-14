"""Tests for notifier_twilio.py — message formatting and config validation."""

from job_hunter.notifier_twilio import _build_messages, _format_job, validate_twilio_config


SAMPLE_JOB = {
    "title": "Backend Dev",
    "company": "FinCo",
    "location": "Remote, US",
    "url": "https://example.com/1",
    "source": "linkedin",
    "salary": "USD 150,000 – 200,000/yearly",
    "match_reason": "Strong fintech match",
    "date_posted": "2026-03-13",
}


class TestFormatJob:
    def test_includes_all_fields(self):
        result = _format_job(1, SAMPLE_JOB)
        assert "Backend Dev" in result
        assert "FinCo" in result
        assert "150,000" in result
        assert "https://example.com/1" in result

    def test_no_salary(self):
        job = {**SAMPLE_JOB, "salary": ""}
        result = _format_job(1, job)
        assert "💰" not in result

    def test_truncates_reason(self):
        job = {**SAMPLE_JOB, "match_reason": "A" * 500}
        result = _format_job(1, job)
        assert len(result) < 600


class TestBuildMessages:
    def test_sms_single(self):
        messages = _build_messages([SAMPLE_JOB], max_chars=1600)
        assert len(messages) == 1
        assert "Job Hunter" in messages[0]

    def test_sms_splits(self):
        jobs = [SAMPLE_JOB] * 20
        messages = _build_messages(jobs, max_chars=1600)
        assert len(messages) > 1
        for msg in messages:
            assert len(msg) <= 1600

    def test_whatsapp_longer(self):
        jobs = [SAMPLE_JOB] * 20
        sms_msgs = _build_messages(jobs, max_chars=1600)
        wa_msgs = _build_messages(jobs, max_chars=4096)
        assert len(wa_msgs) <= len(sms_msgs)


class TestValidateTwilioConfig:
    def test_sms_valid(self):
        config = {
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "tok",
            "twilio_from_number": "+1234",
            "twilio_to_number": "+5678",
        }
        assert validate_twilio_config(config, "sms")

    def test_sms_missing_from(self):
        config = {
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "tok",
            "twilio_to_number": "+5678",
        }
        assert not validate_twilio_config(config, "sms")

    def test_whatsapp_valid(self):
        config = {
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "tok",
            "twilio_whatsapp_from": "whatsapp:+1234",
            "twilio_whatsapp_to": "whatsapp:+5678",
        }
        assert validate_twilio_config(config, "whatsapp")

    def test_whatsapp_missing_to(self):
        config = {
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "tok",
            "twilio_whatsapp_from": "whatsapp:+1234",
        }
        assert not validate_twilio_config(config, "whatsapp")

    def test_empty(self):
        assert not validate_twilio_config({}, "sms")
        assert not validate_twilio_config({}, "whatsapp")
