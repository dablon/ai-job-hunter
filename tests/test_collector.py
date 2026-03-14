"""Tests for collector.py — salary parsing, dedup, country resolution."""

import pandas as pd
import pytest

from job_hunter.collector import (
    _build_salary_string,
    _dataframe_to_jobs,
    _resolve_indeed_country,
)


class TestResolveIndeedCountry:
    def test_colombia(self):
        assert _resolve_indeed_country("Colombia") == "Colombia"

    def test_brazil(self):
        assert _resolve_indeed_country("Brazil") == "Brazil"

    def test_brasil_portuguese(self):
        assert _resolve_indeed_country("Brasil") == "Brazil"

    def test_usa(self):
        assert _resolve_indeed_country("USA") == "USA"

    def test_case_insensitive(self):
        assert _resolve_indeed_country("COLOMBIA") == "Colombia"
        assert _resolve_indeed_country("united states") == "USA"

    def test_unknown_passthrough(self):
        assert _resolve_indeed_country("Peru") == "Peru"

    def test_whitespace(self):
        assert _resolve_indeed_country("  Colombia  ") == "Colombia"


class TestBuildSalaryString:
    def test_range(self):
        row = pd.Series({"min_amount": 80000, "max_amount": 120000, "currency": "USD", "interval": "yearly"})
        assert _build_salary_string(row) == "USD 80,000 – 120,000/yearly"

    def test_same_min_max(self):
        row = pd.Series({"min_amount": 100000, "max_amount": 100000, "currency": "USD", "interval": "yearly"})
        assert _build_salary_string(row) == "USD 100,000/yearly"

    def test_min_only(self):
        row = pd.Series({"min_amount": 50000, "max_amount": float("nan"), "currency": "USD", "interval": "yearly"})
        assert _build_salary_string(row) == "USD 50,000+/yearly"

    def test_max_only(self):
        row = pd.Series({"min_amount": float("nan"), "max_amount": 90000, "currency": "EUR", "interval": "monthly"})
        assert _build_salary_string(row) == "Up to EUR 90,000/monthly"

    def test_no_salary(self):
        row = pd.Series({"min_amount": float("nan"), "max_amount": float("nan"), "currency": "", "interval": ""})
        assert _build_salary_string(row) == ""

    def test_missing_columns(self):
        row = pd.Series({"title": "dev"})
        assert _build_salary_string(row) == ""

    def test_defaults_to_usd(self):
        row = pd.Series({"min_amount": 5000, "max_amount": 8000, "currency": None, "interval": ""})
        assert _build_salary_string(row) == "USD 5,000 – 8,000"


class TestDataframeToJobs:
    def _make_df(self, rows):
        return pd.DataFrame(rows)

    def test_basic_conversion(self):
        df = self._make_df([{
            "id": "1",
            "job_url": "https://example.com/1",
            "title": "Backend Dev",
            "company": "Acme",
            "location": "Remote",
            "date_posted": "2026-03-13",
            "source": "linkedin",
            "description": "Build things",
            "min_amount": float("nan"),
            "max_amount": float("nan"),
            "currency": "",
            "interval": "",
        }])
        jobs = _dataframe_to_jobs(df, set())
        assert len(jobs) == 1
        assert jobs[0]["company"] == "Acme"
        assert jobs[0]["salary"] == ""
        assert jobs[0]["description"] == "Build things"

    def test_with_salary(self):
        df = self._make_df([{
            "id": "2",
            "job_url": "https://example.com/2",
            "title": "SRE",
            "company": "BigCo",
            "location": "US",
            "date_posted": "2026-03-13",
            "source": "indeed",
            "description": "SRE role",
            "min_amount": 150000,
            "max_amount": 200000,
            "currency": "USD",
            "interval": "yearly",
        }])
        jobs = _dataframe_to_jobs(df, set())
        assert jobs[0]["salary"] == "USD 150,000 – 200,000/yearly"

    def test_dedup(self):
        df = self._make_df([
            {"id": "1", "job_url": "https://example.com/1", "title": "A", "company": "", "location": "", "date_posted": "", "source": "linkedin", "description": "", "min_amount": float("nan"), "max_amount": float("nan"), "currency": "", "interval": ""},
            {"id": "2", "job_url": "https://example.com/1", "title": "B", "company": "", "location": "", "date_posted": "", "source": "linkedin", "description": "", "min_amount": float("nan"), "max_amount": float("nan"), "currency": "", "interval": ""},
        ])
        jobs = _dataframe_to_jobs(df, set())
        assert len(jobs) == 1

    def test_uses_company_not_company_name(self):
        df = self._make_df([{
            "id": "3",
            "job_url": "https://example.com/3",
            "title": "Dev",
            "company": "CorrectName",
            "location": "",
            "date_posted": "",
            "source": "linkedin",
            "description": "",
            "min_amount": float("nan"),
            "max_amount": float("nan"),
            "currency": "",
            "interval": "",
        }])
        jobs = _dataframe_to_jobs(df, set())
        assert "company" in jobs[0]
        assert "company_name" not in jobs[0]
        assert jobs[0]["company"] == "CorrectName"
