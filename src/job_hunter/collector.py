"""collector.py — Fetches job listings from multiple sources."""

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Lock

import pandas as pd
import requests
from jobspy import scrape_jobs
from jobspy.model import Country

from job_hunter.utils import retry_with_backoff

# ANSI colors (same as main.py)
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"


def colorize(text: str, color: str) -> str:
    """Add color to text if terminal supports it."""
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{Colors.RESET}"


logger = logging.getLogger(__name__)


class Spinner:
    """Thread-safe spinner for collection progress."""

    SPINNER_CHARS = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self):
        self._idx = 0

    def next(self) -> str:
        """Return next spinner character."""
        char = self.SPINNER_CHARS[self._idx]
        self._idx = (self._idx + 1) % len(self.SPINNER_CHARS)
        return char


# Shared spinner instance for progress updates
_spinner = Spinner()


def get_spinner_char() -> str:
    """Return next spinner character (backwards compatibility wrapper)."""
    return _spinner.next()


def _show_progress(source: str, keyword: str, count: int) -> None:
    """Show inline progress update."""
    if sys.stdout.isatty():
        # Clear line and show progress
        sys.stdout.write(f"\r  {get_spinner_char()} {source:12} | {keyword:30} | {count:3} jobs")
        sys.stdout.flush()

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds — doubles each retry (5, 10, 20)

# Errors that are not retryable - skip immediately instead of wasting retries
NON_RETRYABLE_ERRORS = {
    "glassdoor is not available for",
    "glassdoor is not available in",
    "indeed:indeed is not available",
    "linkedin:linkedin is not available",
}

# Errors that indicate a temporary block - skip this site for remaining keywords
RATE_LIMIT_ERRORS = {"429", "rate limit", "too many requests", "503"}

# LinkedIn circuit breaker — stop hammering after too many 429s in a row
LINKEDIN_429_CIRCUIT_BREAK = 5  # consecutive 429s before we skip LinkedIn entirely

GUPY_API_URL = "https://employability-portal.gupy.io/api/v1/jobs"
REMOTEOK_API_URL = "https://remoteok.com/api"
REMOTEOK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
WEWORKREMOTELY_API_URL = "https://weworkremotely.com/api/v1"
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
GUPY_MAX_PAGES = 10  # Maximum pages to fetch per keyword to prevent infinite loops


def _collect_for_location(config: dict, location: str) -> list[dict]:
    """Collect jobs for a single location."""
    location_config = config.copy()
    location_config["location"] = location
    all_jobs: list[dict] = []

    collectors = [
        ("jobspy", _collect_jobspy),
        ("gupy", _collect_gupy),
        ("remoteok", _collect_remoteok),
        ("weworkremotely", _collect_weworkremotely),
        ("jooble", _collect_jooble),
        ("remotive", _collect_remotive),
    ]

    for name, func in collectors:
        try:
            jobs = func(location_config)
            for job in jobs:
                job["search_location"] = location
            all_jobs.extend(jobs)
        except Exception:
            logger.exception("Source '%s' failed for location '%s' — skipping", name, location)

    return all_jobs


def collect_all(config: dict) -> list[dict]:
    """Fetch jobs from all enabled sources and multiple locations.

    Failures in one source do not block others.
    Returns combined list of canonical job dicts.
    """
    # Get locations - support both single location and multiple locations
    locations = config.get("locations", [config.get("location", "Remote")])

    # Handle legacy single location config
    if isinstance(locations, str):
        locations = [locations]

    # Remove duplicates while preserving order
    seen = set()
    unique_locations = []
    for loc in locations:
        loc_lower = loc.lower().strip()
        if loc_lower not in seen:
            seen.add(loc_lower)
            unique_locations.append(loc)

    locations = unique_locations

    # Print header
    print(colorize("  ├─ ", Colors.GRAY) + colorize("jobspy", Colors.BLUE))

    # Single location - use original logic
    if len(locations) == 1:
        return _collect_for_location(config, locations[0])

    # Multi-location - collect in parallel
    all_jobs: list[dict] = []
    total_by_source: dict[str, int] = {}

    def collect_location(loc: str) -> list[dict]:
        return _collect_for_location(config, loc)

    with ThreadPoolExecutor(max_workers=min(4, len(locations))) as executor:
        futures = {executor.submit(collect_location, loc): loc for loc in locations}

        for future in as_completed(futures):
            location = futures[future]
            try:
                jobs = future.result()
                all_jobs.extend(jobs)
                # Count by source
                for job in jobs:
                    source = job.get("source", "unknown")
                    total_by_source[source] = total_by_source.get(source, 0) + 1
                print(colorize("  │       ", Colors.GRAY) +
                      colorize(f"✓ {location:15} → {len(jobs):3} jobs", Colors.GREEN))
            except Exception:
                logger.exception("Location '%s' failed — skipping", location)

    # Print summary by source
    for source, count in sorted(total_by_source.items()):
        print(colorize("  │       ", Colors.GRAY) +
              colorize(f"✓ {source:15} → {count:3} jobs", Colors.GREEN))

    print(colorize("  │", Colors.GRAY))
    print(colorize("  └─ ", Colors.GRAY) + colorize(f"Total: {len(all_jobs)} jobs collected", Colors.GREEN + Colors.BOLD))
    return all_jobs


# ---------------------------------------------------------------------------
# jobspy source
# ---------------------------------------------------------------------------


GLASSDOOR_BACKOFF_DELAY = 15  # seconds after a 429

# Map config location to indeed country codes (lowercase keys = jobspy Country enum compatible)
INDEED_COUNTRY_MAP = {
    # Valid jobspy countries (lowercase keys)
    "argentina": "argentina",
    "australia": "australia",
    "austria": "austria",
    "bahrain": "bahrain",
    "bangladesh": "bangladesh",
    "belgium": "belgium",
    "brazil": "brazil",
    "brasil": "brazil",
    "canada": "canada",
    "chile": "chile",
    "china": "china",
    "colombia": "colombia",
    "costa rica": "costa rica",
    "croatia": "croatia",
    "cyprus": "cyprus",
    "czech republic": "czech republic",
    "czechia": "czechia",
    "denmark": "denmark",
    "ecuador": "ecuador",
    "egypt": "egypt",
    "estonia": "estonia",
    "finland": "finland",
    "france": "france",
    "germany": "germany",
    "greece": "greece",
    "hong kong": "hong kong",
    "hungary": "hungary",
    "india": "india",
    "indonesia": "indonesia",
    "ireland": "ireland",
    "israel": "israel",
    "italy": "italy",
    "japan": "japan",
    "kuwait": "kuwait",
    "latvia": "latvia",
    "lithuania": "lithuania",
    "luxembourg": "luxembourg",
    "malaysia": "malaysia",
    "malta": "malta",
    "mexico": "mexico",
    "morocco": "morocco",
    "netherlands": "netherlands",
    "new zealand": "new zealand",
    "nigeria": "nigeria",
    "norway": "norway",
    "oman": "oman",
    "pakistan": "pakistan",
    "panama": "panama",
    "peru": "peru",
    "philippines": "philippines",
    "poland": "poland",
    "portugal": "portugal",
    "qatar": "qatar",
    "romania": "romania",
    "saudi arabia": "saudi arabia",
    "singapore": "singapore",
    "slovakia": "slovakia",
    "slovenia": "slovenia",
    "south africa": "south africa",
    "south korea": "south korea",
    "spain": "spain",
    "españa": "spain",
    "sweden": "sweden",
    "switzerland": "switzerland",
    "taiwan": "taiwan",
    "thailand": "thailand",
    "türkiye": "türkiye",
    "turkey": "turkey",
    "ukraine": "ukraine",
    "united arab emirates": "united arab emirates",
    "uk": "uk",
    "united kingdom": "uk",
    "usa": "usa",
    "us": "usa",
    "united states": "usa",
    "uruguay": "uruguay",
    "venezuela": "venezuela",
    "vietnam": "vietnam",
    # Aliases
    "méxico": "mexico",
    "latin america": "colombia",
    "latinoamerica": "colombia",
    "latam": "colombia",
    "europe": "uk",
    "worldwide": "usa",
    "remote": "usa",
}


def _resolve_indeed_country(location: str) -> str:
    """Map a config location string to an Indeed country code."""
    return INDEED_COUNTRY_MAP.get(location.strip().lower(), location)


# Thread-safe lock for updating seen_urls
_url_lock = Lock()

# LinkedIn 429 circuit breaker — consecutive rate-limit count (module-level)
_linkedin_429_count = 0

# Max parallel workers for scraping (balance speed vs rate limiting)
MAX_WORKERS = 4


def _scrape_single_job(
    site: str,
    term: str,
    location: str,
    remote_only: bool,
    indeed_country: str,
    seen_urls: set,
) -> tuple[str, str, list[dict]]:
    """Scrape a single site+keyword combination. Returns (site, term, jobs)."""
    global _linkedin_429_count

    # Circuit breaker: if LinkedIn keeps hitting 429s, skip it for the rest of the run
    if site == "linkedin" and _linkedin_429_count >= LINKEDIN_429_CIRCUIT_BREAK:
        logger.warning("  [linkedin] circuit breaker open — skipping '%s' for this run", term)
        return (site, term, [])

    try:
        df: pd.DataFrame = _scrape_with_retries(
            site=site,
            term=term,
            location=location,
            remote_only=remote_only,
            indeed_country=indeed_country,
        )
        with _url_lock:
            jobs = _dataframe_to_jobs(df, seen_urls)
        # Reset 429 counter on success
        if site == "linkedin":
            _linkedin_429_count = 0
        logger.info("  [%s] '%s' -> %d jobs", site, term, len(jobs))
        return (site, term, jobs)
    except Exception as exc:
        exc_str = str(exc).lower()

        # Check for non-retryable errors
        if any(err in exc_str for err in NON_RETRYABLE_ERRORS):
            logger.warning("  [%s] unsupported — skipping: %s", site, exc)
            return (site, term, [])

        # Check for rate limit errors
        if any(err in exc_str for err in RATE_LIMIT_ERRORS):
            if site == "linkedin":
                _linkedin_429_count += 1
                logger.warning(
                    "  [linkedin] rate-limited (%d/%d) — %s",
                    _linkedin_429_count, LINKEDIN_429_CIRCUIT_BREAK, exc
                )
            else:
                logger.warning("  [%s] rate-limited", site)
            return (site, term, [])

        logger.exception("jobspy failed for site='%s' term='%s' — skipping", site, term)
        return (site, term, [])


def _collect_jobspy(config: dict) -> list[dict]:
    """Fetch from job sites via the jobspy library using parallel execution."""
    global _linkedin_429_count

    # Reset LinkedIn circuit breaker at the start of each collection run
    _linkedin_429_count = 0

    keywords: list[str] = config.get("keywords", [])
    location: str = config.get("location", "")
    remote_only: bool = config.get("remote_only", False)
    sites: list[str] = ["linkedin", "indeed", "glassdoor"]

    # Glassdoor doesn't support many countries - skip it for unsupported locations
    GLASSDOOR_UNSUPPORTED = {"colombia", "argentina", "chile", "peru", "mexico", "brazil",
                            "latin america", "latinoamerica", "latam", "europe", "worldwide", "remote"}
    if location.lower().strip() in GLASSDOOR_UNSUPPORTED:
        sites.remove("glassdoor")
        logger.info("[glassdoor] skipped — not available for %s", location)

    indeed_country = _resolve_indeed_country(location)

    seen_urls: set[str] = set()
    combined: list[dict] = []

    # Separate LinkedIn tasks from other sites
    linkedin_tasks = [("linkedin", term) for term in keywords]
    other_tasks = [(site, term) for site in sites if site != "linkedin" for term in keywords]

    # Run other sites (indeed, glassdoor) in parallel — they are more rate-limit friendly
    other_futures = {}
    if other_tasks:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            other_futures = {
                executor.submit(
                    _scrape_single_job,
                    site, term, location, remote_only, indeed_country, seen_urls
                ): (site, term)
                for site, term in other_tasks
            }
            for future in as_completed(other_futures):
                site, term = other_futures[future]
                try:
                    _site, _term, jobs = future.result()
                    combined.extend(jobs)
                except Exception:
                    logger.exception("Task failed for %s/%s", site, term)

    # Run LinkedIn SERIALLY with a delay between each request to avoid 429s
    for term in keywords:
        # Check circuit breaker before each LinkedIn request
        if _linkedin_429_count >= LINKEDIN_429_CIRCUIT_BREAK:
            logger.warning(
                "  [linkedin] circuit breaker open — skipping remaining keywords for this location"
            )
            break

        jobs = _scrape_single_job("linkedin", term, location, remote_only, indeed_country, seen_urls)[2]
        combined.extend(jobs)
        # Delay between LinkedIn requests to respect rate limits
        time.sleep(2)

    return combined


def _scrape_with_retries(
    site: str,
    term: str,
    location: str,
    remote_only: bool,
    indeed_country: str = "colombia",
) -> pd.DataFrame:
    """Call scrape_jobs with retry logic. Raises on exhausted retries."""
    # Validate country against jobspy Country enum before calling scrape_jobs
    # to avoid ValueError crashes for unsupported countries
    try:
        Country.from_string(indeed_country.lower())
    except ValueError:
        logger.warning(
            "  [%s] country '%s' not supported by jobspy — skipping",
            site, indeed_country
        )
        return pd.DataFrame()
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
# RemoteOK source
# ---------------------------------------------------------------------------


def _collect_remoteok(config: dict) -> list[dict]:
    """Fetch jobs from RemoteOK API.

    RemoteOK returns all remote jobs in one call. We filter by keywords.
    """
    keywords: list[str] = [k.lower() for k in config.get("keywords", [])]
    seen_urls: set[str] = set()

    try:
        resp = requests.get(REMOTEOK_API_URL, headers=REMOTEOK_HEADERS, timeout=30)
        resp.raise_for_status()
        raw_jobs: list[dict] = resp.json()

        # First item is usually a tag/info, skip it
        if raw_jobs and "request" in raw_jobs[0]:
            raw_jobs = raw_jobs[1:]

        combined: list[dict] = []
        for raw in raw_jobs:
            job = _remoteok_job_to_canonical(raw, keywords, seen_urls)
            if job:
                combined.append(job)

        return combined
    except Exception:
        logger.exception("RemoteOK collection failed")
        return []


def _remoteok_job_to_canonical(
    raw: dict,
    keywords: list[str],
    seen_urls: set[str],
) -> dict | None:
    """Convert RemoteOK job to canonical format."""
    url = raw.get("url", "")
    if not url:
        url = f"https://remoteok.com/l/{raw.get('id', '')}"
    if not url or url in seen_urls:
        return None
    seen_urls.add(url)

    # Skip jobs older than 24 hours
    date_str = raw.get("date", "")
    if date_str:
        try:
            import email.utils
            parsed = email.utils.parsedate_to_datetime(date_str)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            if parsed.replace(tzinfo=timezone.utc) < cutoff:
                return None
        except Exception:
            pass

    # Check if job matches any keyword
    title = raw.get("position", "").lower()
    company = raw.get("company", "").lower()
    description = raw.get("description", "").lower()
    tags = " ".join(raw.get("tags", [])).lower()

    search_text = f"{title} {company} {description} {tags}"
    if keywords and not any(kw in search_text for kw in keywords):
        return None

    # RemoteOK salary
    salary_min = raw.get("salary_min")
    salary_max = raw.get("salary_max")
    salary = ""
    if salary_min or salary_max:
        if salary_min and salary_max:
            salary = f"${salary_min:,} - ${salary_max:,}"
        elif salary_min:
            salary = f"${salary_min:,}+"
        else:
            salary = f"Up to ${salary_max:,}"

    return {
        "id": str(raw.get("id", "")),
        "title": raw.get("position", ""),
        "company": raw.get("company", ""),
        "url": url,
        "description": raw.get("description", ""),
        "location": "Remote",
        "date_posted": date_str[:10] if date_str else "",
        "source": "remoteok",
        "salary": salary,
    }


# ---------------------------------------------------------------------------
# WeWorkRemotely source
# ---------------------------------------------------------------------------


def _collect_weworkremotely(config: dict) -> list[dict]:
    """Fetch jobs from WeWorkRemotely.

    This requires scraping since they don't have a public API.
    Falls back to jobspy which already covers this site.
    """
    # WeWorkRemotely doesn't have a public API, so we rely on jobspy to cover it
    # This function is here for completeness but just returns empty
    # since jobspy already queries weworkremotely
    logger.info("[weworkremotely] covered by jobspy - skipping direct collection")
    return []


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
    pages_fetched = 0

    while pages_fetched < GUPY_MAX_PAGES:
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
        pages_fetched += 1
        time.sleep(1)

    if pages_fetched >= GUPY_MAX_PAGES:
        logger.warning("Gupy: reached max pages limit (%d) for keyword '%s'", GUPY_MAX_PAGES, keyword)

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


# ---------------------------------------------------------------------------
# Salary Normalization
# ---------------------------------------------------------------------------

SALARY_CONVERSION_TO_USD = {
    "USD": 1.0,
    "US": 1.0,
    "EUR": 1.10,  # 1 EUR = 1.10 USD
    "GBP": 1.27,  # 1 GBP = 1.27 USD
    "COP": 0.00024,  # 1 COP = 0.00024 USD
    "MXN": 0.058,  # 1 MXN = 0.058 USD
    "BRL": 0.20,  # 1 BRL = 0.20 USD
    "CLP": 0.0011,  # 1 CLP = 0.0011 USD
    "ARS": 0.0012,  # 1 ARS = 0.0012 USD
    "PEN": 0.27,  # 1 PEN = 0.27 USD
    "CAD": 0.74,  # 1 CAD = 0.74 USD
    "AUD": 0.65,  # 1 AUD = 0.65 USD
}

SALARY_INTERVAL_MULTIPLIER = {
    "year": 1,
    "yr": 1,
    "month": 12,
    "mo": 12,
    "week": 52,
    "wk": 52,
    "day": 260,  # ~260 working days/year
    "hour": 2080,  # ~2080 hours/year (40hr * 52wk)
}


def normalize_salary(salary_str: str) -> dict | None:
    """Parse and normalize salary string to USD/year.

    Returns dict with: {min_usd, max_usd, currency, interval}
    """
    if not salary_str or salary_str.strip() == "":
        return None

    import re

    salary_str = salary_str.upper().strip()

    # Extract currency
    currency = "USD"
    for curr in SALARY_CONVERSION_TO_USD:
        if curr in salary_str:
            currency = curr
            break

    # Extract numbers - remove all non-digit characters except dots
    numbers = re.findall(r"[\d]+(?:\.\d+)?", salary_str)
    if not numbers:
        return None

    numbers = [float(n) for n in numbers]
    min_amount = min(numbers)
    max_amount = max(numbers) if len(numbers) > 1 else min_amount

    # Extract interval
    interval = "year"
    for intv, mult in SALARY_INTERVAL_MULTIPLIER.items():
        if intv in salary_str.replace(" ", "") or intv.upper() in salary_str:
            interval = intv
            break

    # Convert to USD/year
    rate = SALARY_CONVERSION_TO_USD.get(currency, 1.0)
    mult = SALARY_INTERVAL_MULTIPLIER.get(interval, 1)

    min_usd = int(min_amount * rate * mult)
    max_usd = int(max_amount * rate * mult)

    return {
        "min_usd": min_usd,
        "max_usd": max_usd,
        "currency": currency,
        "interval": interval,
        "original": salary_str,
    }


# ---------------------------------------------------------------------------
# Jooble source
# ---------------------------------------------------------------------------

JOOBLE_API_URL = "https://jooble.org/api"


def _collect_jooble(config: dict) -> list[dict]:
    """Fetch jobs from Jooble API.

    Jooble is a job aggregator with free API.
    API key can be obtained from jooble.org/api-keys
    """
    api_key = config.get("jooble_api_key", "")
    if not api_key:
        logger.info("[jooble] API key not configured - skipping")
        return []

    keywords = config.get("keywords", [])
    location = config.get("location", "Latin America")
    remote_only = config.get("remote_only", False)

    # Build location string for Jooble
    jooble_location = location
    if remote_only:
        jooble_location = "Remote"

    seen_urls: set[str] = set()
    combined: list[dict] = []

    headers = {"Content-Type": "application/json"}

    for keyword in keywords:
        try:
            payload = {
                "keywords": keyword,
                "location": jooble_location,
                "page": 1,
                "pagesize": 20,
            }

            response = requests.post(
                f"{JOOBLE_API_URL}/{api_key}",
                json=payload,
                headers=headers,
                timeout=30,
            )

            if response.status_code != 200:
                logger.warning("[jooble] API returned status %s", response.status_code)
                continue

            data = response.json()
            jobs = data.get("jobs", [])

            for raw in jobs:
                url = raw.get("link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                job = {
                    "id": raw.get("id", url),
                    "title": raw.get("title", ""),
                    "company": raw.get("company", ""),
                    "url": url,
                    "description": raw.get("snippet", ""),
                    "location": raw.get("location", ""),
                    "date_posted": raw.get("updated", "")[:10],
                    "source": "jooble",
                    "salary": raw.get("salary", ""),
                }
                combined.append(job)

            logger.info("  [jooble] '%s' -> %d jobs", keyword, len(jobs))

        except Exception:
            logger.exception("jooble failed for keyword='%s' — skipping", keyword)

    return combined


# ---------------------------------------------------------------------------
# Remotive source
# ---------------------------------------------------------------------------

REMOTIVE_API_URL = "https://remotive.com/api/remote-jobs"
REMOTIVE_MAX_PAGES = 5  # Safety cap on pagination


def _collect_remotive(config: dict) -> list[dict]:
    """Fetch jobs from Remotive API with pagination.

    Remotive has a free API for remote jobs — paginate to get more results.
    Pagination: page=0 returns first batch, increment until jobs array is empty.
    """
    keywords = [k.lower() for k in config.get("keywords", [])]

    seen_urls: set[str] = set()
    combined: list[dict] = []

    try:
        page = 0
        total_fetched = 0
        while page < REMOTIVE_MAX_PAGES:
            params = {"page": page}
            response = requests.get(REMOTIVE_API_URL, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            raw_jobs = data.get("jobs", [])
            if not raw_jobs:
                break

            for raw in raw_jobs:
                title_lower = raw.get("title", "").lower()

                # Filter by keywords
                if keywords and not any(k in title_lower for k in keywords):
                    continue

                url = raw.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                salary = raw.get("salary", "")

                job = {
                    "id": raw.get("id", url),
                    "title": raw.get("title", ""),
                    "company": raw.get("company_name", ""),
                    "url": url,
                    "description": raw.get("description", ""),
                    "location": raw.get("candidate_required_location", "Remote"),
                    "date_posted": raw.get("publication_date", "")[:10],
                    "source": "remotive",
                    "salary": salary,
                    "category": raw.get("category", ""),
                }
                combined.append(job)

            total_fetched = data.get("job-count", 0)
            page += 1

            # Stop when we've fetched all available jobs
            if len(raw_jobs) < 50 or total_fetched == 0:
                break

        logger.info("  [remotive] -> %d jobs matched", len(combined))

    except Exception:
        logger.exception("remotive failed — skipping")

    return combined
