"""Tests for main.py — channel parsing, deduplication, config loading."""

import json
import tempfile
from pathlib import Path

import pytest

from job_hunter.main import _parse_notify_channels, _deduplicate_jobs, _validate_channels


class TestParseNotifyChannels:
    @pytest.fixture
    def config(self):
        return {"discord_webhook_url": "https://hooks.example.com"}

    def test_single(self):
        assert _parse_notify_channels("email", {}) == ["email"]

    def test_multiple(self, config):
        result = _parse_notify_channels("email,telegram,discord", config)
        assert result == ["email", "telegram", "discord"]

    def test_whitespace(self):
        result = _parse_notify_channels("email , telegram , sms", {})
        assert result == ["email", "telegram", "sms"]

    def test_auto_add_discord_fallback(self, config):
        assert _parse_notify_channels("email", config) == ["email", "discord"]

    def test_discord_not_duplicated(self, config):
        assert _parse_notify_channels("email,discord", config) == ["email", "discord"]

    def test_all_channels(self, config):
        result = _parse_notify_channels("email,discord,telegram,sms,whatsapp", config)
        assert len(result) == 5

    def test_invalid_exits(self, config):
        with pytest.raises(SystemExit):
            _parse_notify_channels("email,pigeon", config)


class TestValidateChannels:
    def test_email_unreachable(self):
        config = {
            "email_sender": "a@b.com",
            "email_app_password": "pass",
            "email_recipient": "c@d.com",
            "smtp_host": "127.0.0.1",
            "smtp_port": "19999",
        }
        viable = _validate_channels(["email"], config)
        assert "email" not in viable

    def test_discord_missing_url(self):
        viable = _validate_channels(["discord"], {})
        assert "discord" not in viable

    def test_discord_with_url(self):
        viable = _validate_channels(["discord"], {"discord_webhook_url": "https://hooks.example.com"})
        assert "discord" in viable

    def test_telegram_missing(self):
        viable = _validate_channels(["telegram"], {})
        assert "telegram" not in viable

    def test_sms_missing(self):
        viable = _validate_channels(["sms"], {})
        assert "sms" not in viable

    def test_mixed_some_viable(self):
        config = {"discord_webhook_url": "https://hooks.example.com"}
        viable = _validate_channels(["email", "discord", "telegram"], config)
        assert viable == ["discord"]


class TestDeduplicateJobs:
    def test_removes_sent(self):
        jobs = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
            {"url": "https://c.com", "title": "C"},
        ]
        sent = {"https://a.com", "https://c.com"}
        result = _deduplicate_jobs(jobs, sent)
        assert len(result) == 1
        assert result[0]["title"] == "B"

    def test_empty_sent(self):
        jobs = [{"url": "https://a.com", "title": "A"}]
        result = _deduplicate_jobs(jobs, set())
        assert len(result) == 1

    def test_all_sent(self):
        jobs = [{"url": "https://a.com", "title": "A"}]
        result = _deduplicate_jobs(jobs, {"https://a.com"})
        assert len(result) == 0
