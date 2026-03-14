"""Tests for notifier_discord.py — embed building with salary."""

from job_hunter.notifier_discord import _build_embed


class TestBuildEmbed:
    def test_basic_embed(self):
        job = {
            "title": "Backend Dev",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/1",
            "source": "linkedin",
            "salary": "",
            "match_reason": "Good match",
            "date_posted": "2026-03-13",
        }
        embed = _build_embed(job)
        assert embed["title"] == "Backend Dev"
        assert embed["url"] == "https://example.com/1"
        # 3 base fields: Company, Location, Source + Reason
        field_names = [f["name"] for f in embed["fields"]]
        assert "Company" in field_names
        assert "Location" in field_names
        assert "Source" in field_names
        assert "Salary" not in field_names

    def test_embed_with_salary(self):
        job = {
            "title": "SRE",
            "company": "BigCo",
            "location": "US",
            "url": "https://example.com/2",
            "source": "indeed",
            "salary": "USD 150,000 – 200,000/yearly",
            "match_reason": "",
            "date_posted": "2026-03-12",
        }
        embed = _build_embed(job)
        field_names = [f["name"] for f in embed["fields"]]
        assert "Salary" in field_names
        salary_field = next(f for f in embed["fields"] if f["name"] == "Salary")
        assert "150,000" in salary_field["value"]

    def test_embed_color_linkedin(self):
        job = {"title": "X", "source": "linkedin", "url": "", "company": "", "location": ""}
        embed = _build_embed(job)
        assert embed["color"] == 0x0A66C2
