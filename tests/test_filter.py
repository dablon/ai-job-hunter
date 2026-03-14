"""Tests for filter.py — prompt building, extraction, constraints."""

import pytest

from job_hunter.filter import (
    ApprovedJob,
    FilterResult,
    _build_hard_constraints,
    _build_user_prompt,
    _extract_approved,
    _format_jobs_for_prompt,
)


class TestBuildHardConstraints:
    def test_remote_only(self):
        result = _build_hard_constraints({"remote_only": True})
        assert "REMOTE WORK REQUIRED" in result
        assert "REJECT" in result

    def test_location_no_remote(self):
        result = _build_hard_constraints({"location": "Colombia", "remote_only": False})
        assert "Colombia" in result
        assert "REMOTE" not in result

    def test_remote_overrides_location(self):
        result = _build_hard_constraints({"location": "Colombia", "remote_only": True})
        assert "REMOTE WORK REQUIRED" in result
        # Location constraint should NOT appear when remote_only
        assert "LOCATION" not in result

    def test_empty_config(self):
        result = _build_hard_constraints({})
        assert result == ""


class TestFormatJobsForPrompt:
    def test_basic_format(self):
        jobs = [
            {
                "title": "Backend Dev",
                "company": "Acme",
                "location": "Remote",
                "description": "Build APIs",
                "url": "https://example.com",
            }
        ]
        result = _format_jobs_for_prompt(jobs)
        assert "Job 0" in result
        assert "Backend Dev" in result
        assert "Acme" in result
        assert "Build APIs" in result

    def test_truncates_description(self):
        jobs = [{"title": "X", "company": "Y", "location": "Z", "description": "A" * 3000, "url": ""}]
        result = _format_jobs_for_prompt(jobs)
        # MAX_DESCRIPTION_CHARS = 1500
        assert len(result) < 3000


class TestExtractApproved:
    def test_valid_extraction(self):
        result = FilterResult(approved=[
            ApprovedJob(job_index=0, reason="Good match"),
            ApprovedJob(job_index=2, reason="Fintech"),
        ])
        batch = [
            {"title": "A", "url": "u1"},
            {"title": "B", "url": "u2"},
            {"title": "C", "url": "u3"},
        ]
        approved = _extract_approved(result, batch)
        assert len(approved) == 2
        assert approved[0]["title"] == "A"
        assert approved[0]["match_reason"] == "Good match"
        assert approved[1]["title"] == "C"

    def test_invalid_index_skipped(self):
        result = FilterResult(approved=[
            ApprovedJob(job_index=99, reason="Nope"),
        ])
        batch = [{"title": "A", "url": "u1"}]
        approved = _extract_approved(result, batch)
        assert len(approved) == 0

    def test_empty_approved(self):
        result = FilterResult(approved=[])
        batch = [{"title": "A", "url": "u1"}]
        approved = _extract_approved(result, batch)
        assert len(approved) == 0


class TestBuildUserPrompt:
    def test_includes_profile_and_jobs(self):
        result = _build_user_prompt("Senior architect", "", "Job 0: Backend")
        assert "Senior architect" in result
        assert "Job 0: Backend" in result

    def test_includes_constraints(self):
        result = _build_user_prompt("prof", "- REMOTE REQUIRED", "jobs text")
        assert "HARD CONSTRAINTS" in result
        assert "REMOTE REQUIRED" in result

    def test_no_constraints_section_when_empty(self):
        result = _build_user_prompt("prof", "", "jobs text")
        assert "HARD CONSTRAINTS" not in result
