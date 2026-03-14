"""Tests for notifier_telegram.py — message formatting and config validation."""

from job_hunter.notifier_telegram import _build_messages, _format_job, _esc, validate_telegram_config


SAMPLE_JOB = {
    "title": "Staff Architect",
    "company": "Acme Corp",
    "location": "Remote",
    "url": "https://example.com/1",
    "source": "linkedin",
    "salary": "USD 180,000 – 220,000/yearly",
    "match_reason": "Perfect fintech match",
    "date_posted": "2026-03-13",
}


class TestFormatJob:
    def test_includes_all_fields(self):
        result = _format_job(1, SAMPLE_JOB)
        assert "Staff Architect" in result
        assert "Acme Corp" in result
        assert "180,000" in result
        assert "Linkedin" in result
        assert "https://example.com/1" in result

    def test_no_salary(self):
        job = {**SAMPLE_JOB, "salary": ""}
        result = _format_job(1, job)
        assert "💰" not in result

    def test_escapes_html(self):
        job = {**SAMPLE_JOB, "title": "Dev <script>alert(1)</script>"}
        result = _format_job(1, job)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestBuildMessages:
    def test_single_message(self):
        messages = _build_messages([SAMPLE_JOB], "14/03/2026")
        assert len(messages) == 1
        assert "Job Hunter" in messages[0]
        assert "Staff Architect" in messages[0]

    def test_splits_long_messages(self):
        # Create enough jobs to exceed 4096 chars
        jobs = [SAMPLE_JOB] * 30
        messages = _build_messages(jobs, "14/03/2026")
        assert len(messages) > 1
        for msg in messages:
            assert len(msg) <= 4096


class TestEsc:
    def test_escapes_ampersand(self):
        assert _esc("A & B") == "A &amp; B"

    def test_escapes_angle_brackets(self):
        assert _esc("<b>") == "&lt;b&gt;"


class TestValidateTelegramConfig:
    def test_valid(self):
        assert validate_telegram_config({"telegram_bot_token": "abc", "telegram_chat_id": "123"})

    def test_missing_token(self):
        assert not validate_telegram_config({"telegram_chat_id": "123"})

    def test_missing_chat_id(self):
        assert not validate_telegram_config({"telegram_bot_token": "abc"})

    def test_empty(self):
        assert not validate_telegram_config({})
