"""filter.py — AI-powered job filtering. Supports Anthropic API, opencode CLI, and Minimax."""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

import anthropic
import requests
from pydantic import BaseModel, ValidationError

from job_hunter.utils import retry_with_backoff

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-haiku-4-5"
BATCH_SIZE = 25
MAX_TOKENS = 4096
MAX_DESCRIPTION_CHARS = 1500
BATCH_DELAY_SECONDS = 2
OPENCODE_TIMEOUT = 180  # seconds per batch subprocess call
MINIMAX_TIMEOUT = 90  # seconds per API call
MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # seconds — doubles each retry (5, 10, 20)


class ApprovedJob(BaseModel):
    job_index: int
    reason: str
    score: float = 0.0  # Match score 0-100


class FilterResult(BaseModel):
    approved: list[ApprovedJob]


# Countries that are NOT in the LATAM focus zone (for pre-filtering)
NON_LATAM_RE = re.compile(
    r"\b(usa|united states|u\.s\.|u\.s|uk|united kingdom|england|london|britain|"
    r"israel|germany|france|netherlands|ireland|belgium|austria|switzerland|"
    r"spain|portugal|italy|poland|czech|hungary|romania|bulgaria|"
    r"saudi|uae|qatar|india|china|hong kong|japan|south korea|australia|new zealand|"
    r"indonesia|malaysia|thailand|vietnam|philippines|canada)\b",
    re.IGNORECASE
)

# Only accept jobs from these LATAM country patterns
LATAM_RE = re.compile(
    r"\b(argentina|brazil|brasil|chile|colombia|costa rica|ecuador|mexico|panama|peru|uruguay|venezuela|"
    r"buenos aires|santiago|bogota|bogotá|medellin|medellín|sao paulo|são paulo|rio de janeiro|"
    r"mexico city|guadalajara|monterrey|tijuana|"
    r"quito|lima|montevideo|panama city|ciudad de panama|caracas|"
    r"ar\b|br\b|cl\b|co\b|cr\b|ec\b|mx\b|pa\b|pe\b|uy\b|ve\b)",
    re.IGNORECASE
)

# Specific patterns that are clearly non-LATAM even if they contain LATAM words
REMOTE_NON_LATAM_RE = re.compile(
    r"remote\s*,?\s*(us|united states|u\.s\.|u\.s|a\.s\.?|uk|united kingdom|england|london|britain|"
    r"germany|france|netherlands|ireland|belgium|austria|switzerland|spain|portugal|italy|"
    r"poland|czech|hungary|romania|bulgaria|saudi|uae|qatar|india|china|hong kong|japan|"
    r"south korea|australia|new zealand|indonesia|malaysia|thailand|vietnam|philippines|canada|singapore)",
    re.IGNORECASE
)


def _prefilter_by_geo(jobs: list[dict], config: dict) -> list[dict]:
    """Quick pre-filter to reject jobs with non-LATAM locations before AI filtering.

    This runs before the expensive AI filtering to remove obviously invalid jobs.
    Only active when focus_countries is set in config.
    """
    focus_countries = config.get("focus_countries", [])
    if not focus_countries:
        return jobs  # No geo pre-filtering if no focus zone

    approved = []
    rejected_count = 0
    for job in jobs:
        loc = str(job.get("location", "")).strip()
        loc_lower = loc.lower()


        # "Remote" alone or empty - let AI decide
        if not loc or loc_lower == "remote":
            approved.append(job)
            continue

        # CHECK ORDER: LATAM first (allow), then non-LATAM (reject)

        # 1. Does it match a LATAM pattern? → ALLOW
        if LATAM_RE.search(loc_lower):
            approved.append(job)
            continue

        # 2. Does it match a non-LATAM country name? → REJECT
        if NON_LATAM_RE.search(loc_lower):
            logger.debug(f"Pre-filter rejected (non-LATAM): {job.get('title','')} @ {loc}")
            rejected_count += 1
            continue

        # 3. Does it match "Remote, US/UK/etc." (non-LATAM remote)? → REJECT
        if REMOTE_NON_LATAM_RE.search(loc_lower):
            logger.debug(f"Pre-filter rejected (remote non-LATAM): {job.get('title','')} @ {loc}")
            rejected_count += 1
            continue

        # 3. Check for valid 2-letter LATAM country codes in location string
        valid_codes = {"ar", "br", "cl", "co", "cr", "ec", "mx", "pa", "pe", "uy", "ve"}
        words = loc.replace(",", " ").split()
        code_found = False
        for word in words:
            wc = word.lower().strip()
            if len(wc) == 2 and wc in valid_codes:
                code_found = True
                break

        if code_found:
            approved.append(job)
        else:
            # Unknown location - let AI decide
            approved.append(job)

    if rejected_count > 0:
        logger.info(f"Geo pre-filter rejected {rejected_count} non-LATAM jobs")
    return approved


# ---------------------------------------------------------------------------
# Profile Analysis & Refinement
# ---------------------------------------------------------------------------


def analyze_and_refine_profile(config: dict, provider: str = "minimax") -> dict:
    """Analyze user profile with AI and return refined config.
    
    This step uses AI to:
    - Identify key skills and strengths from the profile
    - Suggest better keywords for job search
    - Refine search criteria for better matches
    
    Returns a modified config with refined profile/keywords.
    """
    profile = config.get("profile", "")
    keywords = config.get("keywords", [])
    location = config.get("location", "")
    remote_only = config.get("remote_only", True)
    
    if provider == "minimax":
        return _analyze_profile_minimax(profile, keywords, location, remote_only, config)
    elif provider == "opencode":
        return _analyze_profile_opencode(profile, keywords, location, remote_only, config)
    else:
        return _analyze_profile_anthropic(profile, keywords, location, remote_only, config)


def _build_filter_prompt(jobs: list[dict], config: dict, strictness: str = "balanced") -> str:
    """Build the filtering prompt for the AI provider.
    
    Returns tuple of (system_prompt, user_prompt)
    """
    profile = config.get("profile", "")
    constraints = _build_hard_constraints(config)
    strictness_instruction = _get_strictness_instruction(strictness)
    full_constraints = f"{constraints}\n{strictness_instruction}" if constraints else strictness_instruction
    
    # Build the job list for the prompt
    job_listings = []
    for i, job in enumerate(jobs):
        title = job.get("title", "N/A")
        company = job.get("company", "N/A")
        location = job.get("location", "N/A")
        salary = job.get("salary", "N/A")
        url = job.get("url", "N/A")
        description = job.get("description", "")[:MAX_DESCRIPTION_CHARS]
        tags = ", ".join(job.get("tags", []))
        
        job_listings.append(
            f"[JOB {i}] {title}\n"
            f"  Company: {company}\n"
            f"  Location: {location}\n"
            f"  Salary: {salary}\n"
            f"  Tags: {tags}\n"
            f"  URL: {url}\n"
            f"  Description: {description}"
        )
    
    jobs_text = "\n\n".join(job_listings)
    
    system_prompt = f"""You are an expert job search advisor helping a highly skilled infrastructure engineer find their next role.

## USER PROFILE
{profile}

## HARD CONSTRAINTS (MUST REJECT if violated)
{full_constraints}

## YOUR TASK
Review each job against the profile and constraints. Be honest — reject jobs that don't fit, even if they're interesting. The user wants quality matches, not quantity.

## OUTPUT FORMAT
You must respond with a JSON object with this exact structure:
{{
  "approved": [
    {{
      "job_index": <number>,
      "reason": "<one sentence explanation of why this is a good fit>",
      "score": <0-100>
    }}
  ]
}}

Respond with ONLY the JSON object, no markdown, no explanation outside the JSON.
"""
    
    user_prompt = f"""Review these {len(jobs)} jobs:

{jobs_text}

Respond with JSON only."""
    
    return system_prompt, user_prompt


def _build_hard_constraints(config: dict) -> str:
    """Build hard constraints string for filtering prompt."""
    lines = []
    
    # Job titles to exclude
    exclude_titles = config.get("exclude_titles", [])
    if exclude_titles:
        lines.append("- EXCLUDED TITLES: Do not recommend any job with these words in the title: " + ", ".join([f'"{t}"' for t in exclude_titles]))

    # Experience level
    experience = config.get("experience_level", "senior")
    if experience == "senior":
        lines.append("- Only accept senior-level (5+ years) or lead positions. REJECT entry-level, junior, internship, or mid-level (0-3 years) roles.")
    elif experience == "mid-senior":
        lines.append("- Only accept mid-senior or senior positions (3+ years). REJECT entry-level and junior roles.")

    # Location constraints
    location = config.get("location", "")
    locations = config.get("locations", [])
    if location:
        lines.append(f"- JOB LOCATION: Must be in or remote to {location}.")
    elif locations:
        lines.append(f"- JOB LOCATIONS: Must be in or remote to one of: {', '.join(locations)}.")

    # Remote only
    remote_only = config.get("remote_only", True)
    if remote_only:
        if location:
            lines.append(
                f"Reject on-site jobs outside {location}."
            )

    # Focus zone locations (LATAM, EUROPE, etc.) - validate job is from one of these countries
    focus_countries = config.get("focus_countries", [])
    if focus_countries:
        countries_str = ", ".join([f'"{c}"' for c in focus_countries])
        lines.append(
            f"- GEOGRAPHIC FILTER: This search is limited to these countries/regions: {countries_str}. "
            f"REJECT any job where the job location or company headquarters is outside these areas. "
            f"Jobs must be physically based in one of these countries, not just 'remote to' them. "
            f"IMPORTANT: 'Remote, US', 'Remote, UK', 'Remote, London', 'Remote, Germany', and similar "
            f"patterns are OUTSIDE the focus zone and MUST be rejected."
        )

    # Company exclusion list
    exclude_companies = config.get("exclude_companies", [])
    if exclude_companies:
        lines.append("- EXCLUDED COMPANIES: Do not recommend jobs from these companies: " + ", ".join([f'"{c}"' for c in exclude_companies]))

    # Keywords filter
    keywords = config.get("keywords", [])
    if keywords:
        lines.append(f"- Target roles should match some of these keywords: {', '.join(keywords)}")

    # Salary constraints
    salary_min = config.get("salary_min", 0)
    salary_max = config.get("salary_max", 0)
    if salary_min > 0 or salary_max > 0:
        salary_line = "- SALARY: "
        if salary_min > 0 and salary_max > 0:
            salary_line += f"Must pay between ${salary_min:,}-${salary_max:,}/year"
        elif salary_min > 0:
            salary_line += f"Must pay at least ${salary_min:,}/year"
        elif salary_max > 0:
            salary_line += f"Must pay no more than ${salary_max:,}/year"
        lines.append(salary_line)

    return "\n".join(lines)


def _get_strictness_instruction(strictness: str) -> str:
    """Get additional instructions based on strictness level."""
    if strictness == "loose":
        return ("- STRICTNESS: Be lenient. Accept any job that could possibly be a fit, even with minor "
                "mismatches. Focus on catching clearly bad fits. Better to include a marginal job than miss a good one.")
    elif strictness == "strict":
        return ("- STRICTNESS: Be very selective. Only accept jobs with strong matches across most criteria. "
                "Reject anything with significant gaps. The user values quality over quantity.")
    else:  # balanced
        return ("- STRICTNESS: Be balanced. Accept jobs with reasonable fit, where most criteria align. "
                "Reject only when there are meaningful mismatches.")


def _parse_filter_response(response: str) -> list[ApprovedJob]:
    """Parse AI response into ApprovedJob objects."""
    try:
        # Try to find JSON in the response
        json_match = None
        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('{') and not json_match:
                # Find the end of the JSON object
                depth = 0
                start = line.find('{')
                end = start
                for i, c in enumerate(line):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if depth == 0:
                    json_str = line[start:end]
                    json_match = json_str
                    break
        
        if not json_match:
            # Try to extract from anywhere in the response
            import re
            json_match = re.search(r'\{.*"approved".*\}', response, re.DOTALL)
            if json_match:
                json_match = json_match.group(0)
        
        if json_match:
            data = json.loads(json_match)
            approved = [ApprovedJob(**item) for item in data.get("approved", [])]
            return approved
        
        logger.warning("Could not parse JSON from filter response")
        logger.debug(f"Response was: {response[:500]}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}")
        logger.debug(f"Response was: {response[:500]}")
        return []
    except Exception as e:
        logger.warning(f"Error parsing filter response: {e}")
        logger.debug(f"Response was: {response[:500]}")
        return []


def filter_jobs(
    jobs: list[dict], config: dict, provider: str = "anthropic"
) -> list[dict]:
    """Filter jobs using the specified AI provider.

    Returns approved jobs, each enriched with a 'reason' field and 'match_score'.
    Raises RuntimeError if all batches fail or a permanent error occurs.

    The filter_strictness config option controls how selective the AI is:
    - "loose": Accept any job that could possibly be a fit (recommended for initial search)
    - "balanced": Reject jobs with significant mismatches
    - "strict": Only accept jobs with strong matches
    """
    if not jobs:
        logger.info("No jobs to filter")
        return []

    # Pre-filter by geography before expensive AI filtering
    jobs = _prefilter_by_geo(jobs, config)

    profile = config.get("profile", "")
    constraints = _build_hard_constraints(config)

    # Get strictness level from config
    strictness = config.get("filter_strictness", "balanced")
    strictness_instruction = _get_strictness_instruction(strictness)

    # Add strictness to constraints
    full_constraints = f"{constraints}\n{strictness_instruction}" if constraints else strictness_instruction

    if provider == "opencode":
        return _filter_jobs_opencode(jobs, config, full_constraints, strictness)
    elif provider == "minimax":
        return _filter_jobs_minimax(jobs, config, full_constraints, strictness)
    else:
        return _filter_jobs_anthropic(jobs, config, full_constraints, strictness)


def _filter_jobs_anthropic(jobs: list[dict], config: dict, constraints: str, strictness: str) -> list[dict]:
    """Filter jobs using Anthropic Claude."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    approved_jobs = []
    total_batches = (len(jobs) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num in range(total_batches):
        batch_start = batch_num * BATCH_SIZE
        batch_end = min(batch_start + BATCH_SIZE, len(jobs))
        batch = jobs[batch_start:batch_end]

        logger.info(f"[anthropic] Batch {batch_num + 1}/{total_batches} ({len(batch)} jobs)")

        try:
            result = _filter_batch_anthropic(client, batch, config, constraints, strictness)
            approved_jobs.extend(result)
            
            if batch_num < total_batches - 1:
                time.sleep(BATCH_DELAY_SECONDS)
        except Exception as e:
            logger.error(f"Batch {batch_num + 1} failed: {e}")
            if batch_num == total_batches - 1:
                raise

    return approved_jobs


def _filter_batch_anthropic(client, batch: list[dict], config: dict, constraints: str, strictness: str) -> list[dict]:
    """Filter a single batch using Anthropic."""
    profile = config.get("profile", "")
    
    system_prompt, user_prompt = _build_filter_prompt(batch, config, strictness)
    
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    
    approved = _parse_filter_response(response.content[0].text)
    
    # Build result with full job data
    result = []
    for item in approved:
        if 0 <= item.job_index < len(batch):
            job = batch[item.job_index].copy()
            job["reason"] = item.reason
            job["match_score"] = item.score
            result.append(job)
    
    return result


def _filter_jobs_minimax(jobs: list[dict], config: dict, constraints: str, strictness: str) -> list[dict]:
    """Filter jobs using Minimax API."""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")

    group_id = os.environ.get("MINIMAX_GROUP_ID", "")
    if not group_id:
        raise RuntimeError("MINIMAX_GROUP_ID not set")

    approved_jobs = []
    total_batches = (len(jobs) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num in range(total_batches):
        batch_start = batch_num * BATCH_SIZE
        batch_end = min(batch_start + BATCH_SIZE, len(jobs))
        batch = jobs[batch_start:batch_end]

        logger.info(f"[minimax] Batch {batch_num + 1}/{total_batches} ({len(batch)} jobs)")

        try:
            result = _filter_batch_minimax(api_key, group_id, batch, config, strictness)
            approved_jobs.extend(result)
            
            if batch_num < total_batches - 1:
                time.sleep(BATCH_DELAY_SECONDS)
        except Exception as e:
            logger.error(f"Batch {batch_num + 1} failed: {e}")
            if batch_num == total_batches - 1:
                raise

    return approved_jobs


def _filter_batch_minimax(api_key: str, group_id: str, batch: list[dict], config: dict, strictness: str) -> list[dict]:
    """Filter a single batch using Minimax API."""
    system_prompt, user_prompt = _build_filter_prompt(batch, config, strictness)
    
    url = f"https://api.minimax.chat/v1/text/chatcompletion_pro?GroupId={group_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "abab6.5s-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "max_tokens": MAX_TOKENS
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=MINIMAX_TIMEOUT)
    
    if response.status_code != 200:
        raise RuntimeError(f"Minimax API error: {response.status_code} {response.text}")
    
    data = response.json()
    response_text = data["choices"][0]["message"]["content"]
    
    approved = _parse_filter_response(response_text)
    
    result = []
    for item in approved:
        if 0 <= item.job_index < len(batch):
            job = batch[item.job_index].copy()
            job["reason"] = item.reason
            job["match_score"] = item.score
            result.append(job)
    
    return result


def _filter_jobs_opencode(jobs: list[dict], config: dict, constraints: str, strictness: str) -> list[dict]:
    """Filter jobs using opencode CLI."""
    approved_jobs = []
    total_batches = (len(jobs) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num in range(total_batches):
        batch_start = batch_num * BATCH_SIZE
        batch_end = min(batch_start + BATCH_SIZE, len(jobs))
        batch = jobs[batch_start:batch_end]

        logger.info(f"[opencode] Batch {batch_num + 1}/{total_batches} ({len(batch)} jobs)")

        try:
            result = _filter_batch_opencode(batch, config, strictness)
            approved_jobs.extend(result)
            
            if batch_num < total_batches - 1:
                time.sleep(BATCH_DELAY_SECONDS)
        except Exception as e:
            logger.error(f"Batch {batch_num + 1} failed: {e}")
            if batch_num == total_batches - 1:
                raise

    return approved_jobs


def _filter_batch_opencode(batch: list[dict], config: dict, strictness: str) -> list[dict]:
    """Filter a single batch using opencode CLI."""
    system_prompt, user_prompt = _build_filter_prompt(batch, config, strictness)
    
    model = os.environ.get("OPENCODE_MODEL", "anthropic/claude-haiku-4-5")
    timeout = OPENCODE_TIMEOUT
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(f"SYSTEM: {system_prompt}\n\nUSER: {user_prompt}")
        temp_file = f.name
    
    try:
        result = subprocess.run(
            ["opencode", "-m", model, "--no-stream", temp_file],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Opencode error: {result.stderr}")
        
        approved = _parse_filter_response(result.stdout)
        
        result_jobs = []
        for item in approved:
            if 0 <= item.job_index < len(batch):
                job = batch[item.job_index].copy()
                job["reason"] = item.reason
                job["match_score"] = item.score
                result_jobs.append(job)
        
        return result_jobs
    finally:
        os.unlink(temp_file)
