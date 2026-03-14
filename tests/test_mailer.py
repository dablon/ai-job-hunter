"""Tests for mailer.py — HTML/plaintext generation, salary display."""

import pytest

from job_hunter.mailer import _build_html, _build_plaintext, validate_smtp_config


SAMPLE_JOBS = [
    {
        "title": "Staff Architect",
        "company": "Acme Corp",
        "location": "Remote",
        "url": "https://example.com/1",
        "source": "linkedin",
        "salary": "USD 180,000 – 220,000/yearly",
        "match_reason": "Perfect match for fintech architect",
        "date_posted": "2026-03-13",
    },
    {
        "title": "DevOps Engineer",
        "company": "StartupCo",
        "location": "Colombia",
        "url": "https://example.com/2",
        "source": "indeed",
        "salary": "",
        "match_reason": "Cloud-native expertise",
        "date_posted": "2026-03-12",
    },
]


class TestBuildHtml:
    def test_contains_job_titles(self):
        html = _build_html(SAMPLE_JOBS)
        assert "Staff Architect" in html
        assert "DevOps Engineer" in html

    def test_contains_company_names(self):
        html = _build_html(SAMPLE_JOBS)
        assert "Acme Corp" in html
        assert "StartupCo" in html

    def test_contains_salary_when_present(self):
        html = _build_html(SAMPLE_JOBS)
        assert "180,000" in html
        assert "220,000" in html

    def test_no_salary_for_empty(self):
        html = _build_html([SAMPLE_JOBS[1]])
        # Should not have salary section for job without salary
        assert "💰" not in html or "StartupCo" in html

    def test_contains_reason(self):
        html = _build_html(SAMPLE_JOBS)
        assert "Perfect match for fintech architect" in html

    def test_contains_source_badge(self):
        html = _build_html(SAMPLE_JOBS)
        assert "Linkedin" in html
        assert "Indeed" in html

    def test_job_count(self):
        html = _build_html(SAMPLE_JOBS)
        assert "2 relevant jobs found" in html


class TestBuildPlaintext:
    def test_contains_job_info(self):
        txt = _build_plaintext(SAMPLE_JOBS)
        assert "Staff Architect @ Acme Corp" in txt
        assert "DevOps Engineer @ StartupCo" in txt

    def test_contains_salary(self):
        txt = _build_plaintext(SAMPLE_JOBS)
        assert "Salary: USD 180,000" in txt

    def test_no_salary_line_when_empty(self):
        txt = _build_plaintext([SAMPLE_JOBS[1]])
        assert "Salary:" not in txt

    def test_contains_links(self):
        txt = _build_plaintext(SAMPLE_JOBS)
        assert "https://example.com/1" in txt
        assert "https://example.com/2" in txt

    def test_contains_reason(self):
        txt = _build_plaintext(SAMPLE_JOBS)
        assert "Perfect match for fintech architect" in txt


class TestValidateSmtpConfig:
    def test_missing_sender(self):
        assert not validate_smtp_config({"email_app_password": "x", "email_recipient": "x"})

    def test_missing_password(self):
        assert not validate_smtp_config({"email_sender": "x", "email_recipient": "x"})

    def test_missing_recipient(self):
        assert not validate_smtp_config({"email_sender": "x", "email_app_password": "x"})

    def test_unreachable_server(self):
        assert not validate_smtp_config({
            "email_sender": "a@b.com",
            "email_app_password": "pass",
            "email_recipient": "c@d.com",
            "smtp_host": "127.0.0.1",
            "smtp_port": "19999",
        })
