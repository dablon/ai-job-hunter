"""collector.py — Fetches job listings from multiple sources."""

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from jobspy import scrape_jobs

from job_hunter.utils import retry_with_backoff

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds — doubles each retry (5, 10, 20)

GUPY_API_URL = "https://employability-portal.gupy.io/api/v1/jobs"
GUPY_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://portal.gupy.io",
    "referer": "https://portal.gupy.io/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}
GUPY_LIMIT = 100


def collect_all(config: dict) -> list[dict]:
    """Fetch jobs from all enabled sources.

    Failures in one source do not block others.
    Returns combined list of canonical job dicts.
    """
    all_jobs: list[dict] = []

    collectors = [
        ("jobspy", _collect_jobspy),
        ("gupy", _collect_gupy),
    ]

    for name, func in collectors:
        try:
            jobs = func(config)
            logger.info("Source '%s' returned %d jobs", name, len(jobs))
            all_jobs.extend(jobs)
        except Exception:
            logger.exception("Source '%s' failed — skipping", name)

    logger.info("Total collected: %d jobs", len(all_jobs))
    return all_jobs


# ---------------------------------------------------------------------------
# jobspy source
# ---------------------------------------------------------------------------


GLASSDOOR_BACKOFF_DELAY = 15  # seconds after a 429

# Map config location to indeed country codes
INDEED_COUNTRY_MAP = {
    "colombia": "Colombia",
    "brazil": "Brazil",
    "brasil": "Brazil",
    "argentina": "Argentina",
    "chile": "Chile",
    "mexico": "Mexico",
    "méxico": "Mexico",
    "usa": "USA",
    "united states": "USA",
    "canada": "Canada",
    "uk": "UK",
    "united kingdom": "UK",
}


def _resolve_indeed_country(location: str) -> str:
    """Map a config location string to an Indeed country code."""
    return INDEED_COUNTRY_MAP.get(location.strip().lower(), location)


def _collect_jobspy(config: dict) -> list[dict]:
    """Fetch from job sites via the jobspy library.

    Iterates per site then per keyword so each site failure is isolated.
    """
    keywords: list[str] = config.get("keywords", [])
    location: str = config.get("location", "")
    remote_only: bool = config.get("remote_only", False)
    sites: list[str] = ["linkedin", "indeed", "glassdoor"]
    indeed_country = _resolve_indeed_country(location)

    combined: list[dict] = []
    seen_urls: set[str] = set()
    glassdoor_blocked = False

    for site in sites:
        for i, term in enumerate(keywords):
            if i > 0:
                time.sleep(3)

            if site == "glassdoor" and glassdoor_blocked:
                logger.info("  [glassdoor] '%s' -> skipped (rate-limited)", term)
                continue

            try:
                df: pd.DataFrame = _scrape_with_retries(
                    site=site,
                    term=term,
                    location=location,
                    remote_only=remote_only,
                    indeed_country=indeed_country,
                )
                jobs = _dataframe_to_jobs(df, seen_urls)
                logger.info("  [%s] '%s' -> %d jobs", site, term, len(jobs))
                combined.extend(jobs)
            except Exception as exc:
                if site == "glassdoor" and "429" in str(exc):
                    logger.warning(
                        "  [glassdoor] 429 rate-limited — skipping remaining keywords"
                    )
                    glassdoor_blocked = True
                    time.sleep(GLASSDOOR_BACKOFF_DELAY)
                else:
                    logger.exception(
                        "jobspy failed for site='%s' term='%s' — skipping", site, term
                    )

        time.sleep(5)  # pause between sites to reduce rate limiting

    return combined


def _scrape_with_retries(
    site: str,
    term: str,
    location: str,
    remote_only: bool,
    indeed_country: str = "Colombia",
) -> pd.DataFrame:
    """Call scrape_jobs with retry logic. Raises on exhausted retries."""
    return retry_with_backoff(
        lambda: scrape_jobs(
            site_name=[site],
            search_term=term,
            location=location,
            country_indeed=indeed_country,
            results_wanted=25,
            hours_old=24,
            is_remote=remote_only,
            linkedin_fetch_description=True,
        ),
        max_retries=MAX_RETRIES,
        base_delay=RETRY_BASE_DELAY,
        context=f"{site}/{term}",
    )


# ---------------------------------------------------------------------------
# Gupy source
# ---------------------------------------------------------------------------


def _collect_gupy(config: dict) -> list[dict]:
    """Fetch jobs from Gupy's public API.

    Iterates per keyword; paginates if total > 100.
    Filters to jobs published in the last 24 hours.
    """
    keywords: list[str] = config.get("keywords", [])
    remote_only: bool = config.get("remote_only", False)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    seen_urls: set[str] = set()
    combined: list[dict] = []

    for keyword in keywords:
        try:
            jobs = _fetch_gupy_keyword(keyword, remote_only, cutoff, seen_urls)
            logger.info("  [gupy] '%s' -> %d jobs", keyword, len(jobs))
            combined.extend(jobs)
        except Exception:
            logger.exception("gupy failed for keyword='%s' — skipping", keyword)

    return combined


def _fetch_gupy_keyword(
    keyword: str,
    remote_only: bool,
    cutoff: datetime,
    seen_urls: set[str],
) -> list[dict]:
    """Fetch all pages for a single keyword and return filtered canonical jobs."""
    params: dict = {"jobName": keyword, "limit": GUPY_LIMIT, "offset": 0}
    if remote_only:
        params["workplaceTypes"] = "remote"

    jobs: list[dict] = []

    while True:
        resp = requests.get(
            GUPY_API_URL, params=params, headers=GUPY_HEADERS, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        raw_jobs: list[dict] = data.get("data", [])
        total: int = data.get("pagination", {}).get("total", len(raw_jobs))

        for raw in raw_jobs:
            job = _gupy_job_to_canonical(raw, cutoff, seen_urls)
            if job:
                jobs.append(job)

        fetched_so_far = params["offset"] + len(raw_jobs)
        if fetched_so_far >= total or len(raw_jobs) < GUPY_LIMIT:
            break

        params["offset"] += GUPY_LIMIT
        time.sleep(1)

    return jobs


def _gupy_job_to_canonical(
    raw: dict,
    cutoff: datetime,
    seen_urls: set[str],
) -> dict | None:
    """Convert a Gupy API job dict to canonical format. Returns None if filtered out."""
    url = raw.get("jobUrl", "")
    if not url or url in seen_urls:
        return None

    published_str = raw.get("publishedDate", "")
    if not published_str:
        return None
    try:
        published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published < cutoff:
        return None

    seen_urls.add(url)

    city = raw.get("city") or ""
    state = raw.get("state") or ""
    location = (
        f"{city}, {state}".strip(", ") if city or state else raw.get("country") or ""
    )

    # Gupy salary
    salary = ""
    salary_from = raw.get("salaryFrom")
    salary_to = raw.get("salaryTo")
    if salary_from or salary_to:
        if salary_from and salary_to:
            salary = f"BRL {int(salary_from):,} – {int(salary_to):,}/month"
        elif salary_from:
            salary = f"BRL {int(salary_from):,}+/month"
        else:
            salary = f"Up to BRL {int(salary_to):,}/month"

    return {
        "id": str(raw.get("id", "")),
        "title": raw.get("name", ""),
        "company": raw.get("careerPageName", ""),
        "url": url,
        "description": raw.get("description", ""),
        "location": location,
        "date_posted": published.date().isoformat(),
        "source": "gupy",
        "salary": salary,
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_salary_string(row: pd.Series) -> str:
    """Build a human-readable salary string from jobspy columns."""
    min_amt = row.get("min_amount") if "min_amount" in row else None
    max_amt = row.get("max_amount") if "max_amount" in row else None
    currency = row.get("currency", "") if "currency" in row else ""
    interval = row.get("interval", "") if "interval" in row else ""

    if pd.isna(min_amt) and pd.isna(max_amt):
        return ""

    currency = str(currency or "USD").upper()
    interval = str(interval or "").lower()

    parts = []
    if pd.notna(min_amt) and pd.notna(max_amt):
        min_v, max_v = int(min_amt), int(max_amt)
        if min_v == max_v:
            parts.append(f"{currency} {min_v:,}")
        else:
            parts.append(f"{currency} {min_v:,} – {max_v:,}")
    elif pd.notna(min_amt):
        parts.append(f"{currency} {int(min_amt):,}+")
    else:
        parts.append(f"Up to {currency} {int(max_amt):,}")

    if interval:
        parts.append(f"/{interval}")

    return "".join(parts)


def _dataframe_to_jobs(df: pd.DataFrame, seen_urls: set[str]) -> list[dict]:
    """Convert a jobspy DataFrame to canonical job dicts, skipping duplicates."""
    jobs: list[dict] = []
    for _, row in df.iterrows():
        url = str(row.get("job_url", "") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        title = row.get("title") or ""
        company = row.get("company") or ""
        location = row.get("location") or ""
        description = str(row.get("description", "") or "").strip()[:5000]

        job = {
            "id": row.get("id") or url,
            "title": title,
            "company": company,
            "url": url,
            "description": description,
            "location": location,
            "date_posted": str(row.get("date_posted", ""))[:10],
            "source": row.get("source", "unknown"),
            "salary": _build_salary_string(row),
        }
        jobs.append(job)

    return jobs
