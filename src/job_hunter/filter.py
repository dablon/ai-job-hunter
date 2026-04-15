"""filter.py — AI-powered job filtering. Supports Anthropic API, opencode CLI, and Minimax."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time

import anthropic
import requests
from pydantic import BaseModel, ValidationError

from job_hunter.utils import retry_with_backoff

import re

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
    r"\b(usa|united states|uk|united kingdom|london|lisbon|israel|berlin|"
    r"paris|amsterdam|dublin|singapore|australia|canada|india|germany|"
    r"france|netherlands|ireland|saudi|uae|japan|china|hong kong|hongkong|"
    r"south korea|new zealand|indonesia|malaysia|thailand|vietnam|philippines)",
    re.IGNORECASE,
)


def _prefilter_by_geo(jobs: list[dict], config: dict) -> list[dict]:
    """Quick pre-filter to reject jobs with non-LATAM locations before AI filtering.

    This runs before the expensive AI filtering to remove obviously invalid jobs.
    Only active when focus_countries is set in config.
    """
    focus_countries = config.get("focus_countries", [])
    if not focus_countries:
        return jobs  # No geo pre-filtering if no focus zone

    # Build a set of valid country name/codes for fast lookup
    valid_countries = set()
    for c in focus_countries:
        valid_countries.add(c.lower())
        # Add common variations
        if c.lower() == "argentina":
            valid_countries.update(["ar", "argentina", "buenos aires", "cordoba"])
        elif c.lower() == "brazil":
            valid_countries.update(["br", "brazil", "brasil", "brasília", "são paulo", "rio de janeiro"])
        elif c.lower() == "chile":
            valid_countries.update(["cl", "chile", "santiago"])
        elif c.lower() == "colombia":
            valid_countries.update(["co", "colombia", "bogotá", "bogota", "medellín", "medellin"])
        elif c.lower() == "costa rica":
            valid_countries.update(["cr", "costa rica", "san josé", "san jose"])
        elif c.lower() == "ecuador":
            valid_countries.update(["ec", "ecuador", "quito"])
        elif c.lower() == "mexico":
            valid_countries.update(["mx", "mexico", "guadalajara", "monterrey", "ciudad de méxico"])
        elif c.lower() == "panama":
            valid_countries.update(["pa", "panama", "ciudad de panamá"])
        elif c.lower() == "peru":
            valid_countries.update(["pe", "peru", "lima"])
        elif c.lower() == "uruguay":
            valid_countries.update(["uy", "uruguay", "montevideo"])
        elif c.lower() == "venezuela":
            valid_countries.update(["ve", "venezuela"])

    approved = []
    rejected_count = 0
    for job in jobs:
        loc = str(job.get("location", "")).strip()

        # Empty locations and "Remote" are passed to AI for final decision
        if not loc or loc.lower() == "remote":
            approved.append(job)
            continue

        # Check for obviously non-LATAM country names/codes
        loc_lower = loc.lower()
        if NON_LATAM_RE.search(loc_lower):
            logger.debug(f"Pre-filter rejected (non-LATAM): {job.get('title','')} @ {loc}")
            rejected_count += 1
            continue

        # Extract potential country code (last 2-3 characters after comma or standalone)
        words = loc.replace(",", " ").split()
        valid = False
        for word in words:
            word_clean = word.lower().strip()
            # Check 2-letter country codes
            if len(word_clean) == 2 and word_clean in valid_countries:
                valid = True
                break
            # Check country names
            if word_clean in valid_countries:
                valid = True
                break
        # Also check full location string against valid country names
        if not valid:
            for vc in valid_countries:
                if vc in loc_lower and len(vc) > 2:  # Only match full country names, not 2-letter codes
                    valid = True
                    break

        if valid:
            approved.append(job)
        else:
            # Location not recognized as LATAM - let AI decide
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
    
    if not profile:
        logger.warning("No profile found, skipping AI analysis")
        return config
    
    if provider == "minimax":
        return _analyze_profile_minimax(config)
    else:
        return _analyze_profile_anthropic(config)


def _analyze_profile_minimax(config: dict) -> dict:
    """Use Minimax to analyze and refine the profile."""
    import requests
    
    api_key = config.get("minimax_api_key", "")
    model = config.get("minimax_model", "MiniMax-M2.5")
    
    if not api_key:
        logger.warning("No minimax_api_key, skipping profile analysis")
        return config
    
    profile = config.get("profile", "")
    keywords = config.get("keywords", [])
    
    prompt = f"""Analyze this job seeker profile and provide refined search parameters.

## Current Profile:
{profile}

## Current Keywords:
{", ".join(keywords)}

## Current Preferences:
- Location: {config.get("location", "Any")}
- Remote only: {config.get("remote_only", True)}

Respond with ONLY a JSON object containing:
{{
  "refined_profile": "2-3 sentence summary of the candidate's ideal job targets",
  "suggested_keywords": ["keyword1", "keyword2", ...],
  "search_tips": "2-3 specific tips for finding matching jobs"
}}

Focus on: tech stack, seniority level, industry fit, and role types."""

    url = "https://api.minimax.io/v1/text/chatcompletion_v2"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a career advisor and job search expert. Analyze profiles and suggest optimized job search strategies."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 800,
        "temperature": 0.3,
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        logger.info(f"AI Profile Analysis response: {content[:200]}...")
        
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Try to find valid JSON in the response
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON object from potentially corrupted response
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start:end+1]
                try:
                    result = json.loads(content)
                except json.JSONDecodeError:
                    raise ValueError(f"Could not parse JSON from response: {content[:200]}...")
            else:
                raise ValueError(f"No JSON object found in response: {content[:200]}...")
        
        refined_config = config.copy()
        refined_config["profile"] = result.get("refined_profile", profile)
        refined_config["keywords"] = result.get("suggested_keywords", keywords)
        refined_config["search_tips"] = result.get("search_tips", "")
        
        logger.info(f"Profile refined. New keywords: {refined_config['keywords']}")
        return refined_config
        
    except Exception as e:
        logger.warning(f"Profile analysis failed: {e}, using original config")
        return config


def _analyze_profile_anthropic(config: dict) -> dict:
    """Use Anthropic to analyze and refine the profile."""
    api_key = config.get("anthropic_api_key", "")
    
    if not api_key:
        logger.warning("No anthropic_api_key, skipping profile analysis")
        return config
    
    prompt = f"""Analyze this job seeker profile and provide refined search parameters.

## Current Profile:
{config.get("profile", "")}

## Current Keywords:
{", ".join(config.get("keywords", []))}

Respond with ONLY a JSON object containing:
{{
  "refined_profile": "2-3 sentence summary",
  "suggested_keywords": ["keyword1", "keyword2", ...]
}}"""

    client = anthropic.Anthropic(api_key=api_key)
    
    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        content = message.content[0].text
        result = json.loads(content)
        
        refined_config = config.copy()
        refined_config["profile"] = result.get("refined_profile", config.get("profile", ""))
        refined_config["keywords"] = result.get("suggested_keywords", config.get("keywords", []))
        
        return refined_config
        
    except Exception as e:
        logger.warning(f"Profile analysis failed: {e}, using original config")
        return config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
        batch_fn = _make_opencode_batch_fn(config, profile, full_constraints)
        return _filter_in_batches(jobs, batch_fn, provider)
    elif provider == "minimax":
        batch_fn = _make_minimax_batch_fn(config, profile, full_constraints)
        return _filter_in_batches(jobs, batch_fn, provider)
    else:
        batch_fn = _make_anthropic_batch_fn(config, profile, full_constraints)
        return _filter_in_batches(
            jobs, batch_fn, provider, abort_on=(anthropic.BadRequestError,)
        )


def _get_strictness_instruction(strictness: str) -> str:
    """Get the AI instruction based on strictness level."""
    instructions = {
        "loose": (
            "FILTER STRICTNESS (LOOSE): Approve any job that has ANY potential fit. "
            "Even partial matches are worth showing. Don't filter out jobs - let the user decide."
        ),
        "balanced": (
            "FILTER STRICTNESS (BALANCED): Reject only jobs with clear mismatches. "
            "Consider: skills gap, seniority mismatch, wrong location. "
            "If there's any reasonable chance, approve it."
        ),
        "strict": (
            "FILTER STRICTNESS (STRICT): Only approve jobs with strong matches. "
            "Reject if: different tech stack, wrong seniority level, missing key skills. "
            "Focus on high-quality matches only."
        ),
    }
    return instructions.get(strictness, instructions["balanced"])
# ---------------------------------------------------------------------------
# Shared batch loop
# ---------------------------------------------------------------------------


def _filter_in_batches(
    jobs: list[dict],
    batch_fn,
    provider: str,
    abort_on: tuple[type[Exception], ...] = (),
) -> list[dict]:
    """Run *batch_fn* over job batches, collecting approved results.

    *abort_on* exceptions propagate immediately (permanent errors).
    All other exceptions are logged and the batch is skipped.
    """
    approved_jobs: list[dict] = []
    batches_succeeded = 0
    total_batches = (len(jobs) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        logger.info(
            "[%s] Batch %d/%d (%d jobs)", provider, batch_num, total_batches, len(batch)
        )

        if i > 0:
            time.sleep(BATCH_DELAY_SECONDS)

        try:
            result = batch_fn(batch)
            batches_succeeded += 1
            approved_jobs.extend(_extract_approved(result, batch))
        except abort_on as exc:
            logger.error("Permanent API error — aborting: %s", exc)
            raise RuntimeError(f"AI filtering aborted: {exc}") from exc
        except Exception:
            logger.exception("[%s] Batch %d failed — skipping", provider, batch_num)

    if batches_succeeded == 0:
        raise RuntimeError(f"All {provider} batches failed")

    logger.info(
        "AI filter (%s): %d in -> %d approved", provider, len(jobs), len(approved_jobs)
    )
    return approved_jobs


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------


def _build_hard_constraints(config: dict) -> str:
    """Build an explicit constraints block from config settings.

    These are injected into every prompt as non-negotiable rejection rules.
    """
    lines: list[str] = []

    if config.get("remote_only"):
        lines.append(
            "- REMOTE WORK: If the job says 'remote' or 'work from anywhere' or has no location, "
            "ASSUME it's remote. Only reject if it explicitly says 'on-site', 'hybrid', "
            "'in-office', or 'must work from [city]'. "
            "If location is empty or says 'remote worldwide' — APPROVE."
        )

    location = config.get("location", "").strip()
    if location and not config.get("remote_only"):
        lines.append(
            f"- LOCATION: the user is in {location}. "
            f"Reject on-site jobs outside {location}."
        )

    # Focus zone locations (LATAM, EUROPE, etc.) - validate job is from one of these countries
    focus_countries = config.get("focus_countries", [])
    if focus_countries:
        countries_str = ", ".join([f'"{c}"' for c in focus_countries])
        lines.append(
            f"- GEOGRAPHIC FILTER: This search is limited to these countries/regions: {countries_str}. "
            f"REJECT any job where the job location or company headquarters is outside these areas. "
            f"Jobs must be physically based in one of these countries, not just 'remote to' them."
        )

    # Company exclusion list
    exclude_companies = config.get("exclude_companies", [])
    if exclude_companies:
        companies_str = ", ".join([f'"{c}"' for c in exclude_companies])
        lines.append(
            f"- EXCLUDED COMPANIES: REJECT jobs from these companies: {companies_str}"
        )

    # Keyword exclusion list
    exclude_keywords = config.get("exclude_keywords", [])
    if exclude_keywords:
        keywords_str = ", ".join([f'"{k}"' for k in exclude_keywords])
        lines.append(
            f"- EXCLUDED KEYWORDS: REJECT jobs containing these keywords: {keywords_str}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a helpful job-matching assistant. Respond ONLY with a JSON object — "
    "no markdown, no explanation, no text before or after the JSON.\n\n"
    "APPROVE a job if there's ANY reasonable chance the user could land it. "
    "Be VERY generous — if there's a fit, include it. Don't be overly picky.\n\n"
    "REJECT only if:\n"
    "- The job explicitly requires on-site or hybrid work (not remote)\n"
    "- The job requires a location the user cannot work from AND is not remote\n"
    "- The job requires a language the user doesn't speak\n"
    "- Company is in your exclusion list\n"
    "- Job contains excluded keywords\n\n"
    "DO NOT REJECT for:\n"
    "- Seniority mismatch (a Principal can do Staff/Senior roles)\n"
    "- Different but related tech stack (if they know .NET, accept Java/Python jobs too)\n"
    "- Missing years of experience — if they have 20 years, they can do anything\n"
    "- Vague or missing location — assume remote if not stated\n"
    "- Promoted/sponsored listings — still worth showing\n\n"
    "For each approved job, provide:\n"
    "- job_index: The index of the job\n"
    "- reason: Explain WHY the job could be a good match\n"
    "- score: Rate match quality 0-100 (100 = perfect match)\n\n"
    'Required format: {"approved": [{"job_index": 0, "reason": "reason in English", "score": 85}]}\n'
    'If nothing matches: {"approved": []}'
)


def _build_job_filter_prompt(
    profile: str,
    constraints: str,
    jobs_text: str,
    include_system_in_user: bool = True,
) -> tuple[str, str]:
    """Build system and user prompts for job filtering.

    Args:
        profile: User's profile text
        constraints: Hard constraints string
        jobs_text: Formatted job listings
        include_system_in_user: If True, include system prompt in user message (for Minimax)

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    hard_constraints_section = (
        f"\n\nHARD CONSTRAINTS (non-negotiable — REJECT if violated):\n{constraints}"
        if constraints
        else ""
    )

    user_prompt = (
        "IMPORTANT: Your response must be ONLY a valid JSON object. "
        "No markdown, no explanation, no text before or after the JSON.\n\n"
        "Task: Evaluate the job postings below against the user's profile. "
        "APPROVE any job that could possibly be a fit. Be VERY generous.\n\n"
        "Only REJECT if:\n"
        "- Job explicitly requires on-site or hybrid (not remote)\n"
        "- Job is in wrong language or requires relocation you can't do\n\n"
        "DO NOT reject for: seniority mismatch, different tech stack, missing years of experience, "
        "vague location, or promoted listings.\n\n"
        "Required JSON format:\n"
        '{"approved": [{"job_index": 0, "reason": "reason in English"}, ...]}\n'
        'If nothing matches: {"approved": []}\n\n'
        f"## User Profile\n{profile}"
        f"{hard_constraints_section}\n\n"
        f"## Jobs to Evaluate\n{jobs_text}\n\n"
        "Respond with ONLY the JSON object."
    )

    if include_system_in_user:
        # For Minimax/opencode: embed system in user prompt
        return ("", user_prompt)

    # For Anthropic: separate system and user prompts
    return (SYSTEM_PROMPT, user_prompt)


def _build_user_prompt(profile: str, constraints: str, jobs_text: str) -> str:
    hard_constraints_section = (
        f"\n\nHARD CONSTRAINTS (non-negotiable — REJECT if violated):\n{constraints}"
        if constraints
        else ""
    )
    return (
        f"## User Profile\n{profile}"
        f"{hard_constraints_section}"
        f"\n\n## Jobs to Evaluate\n{jobs_text}"
    )


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


def _make_anthropic_batch_fn(config: dict, profile: str, constraints: str):
    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        raise RuntimeError("anthropic_api_key not set in config")
    client = anthropic.Anthropic(api_key=api_key)

    def batch_fn(batch: list[dict]) -> FilterResult:
        jobs_text = _format_jobs_for_prompt(batch)
        user_prompt = _build_user_prompt(profile, constraints, jobs_text)

        def _call() -> FilterResult:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": "{"},
                ],
            )
            raw_text = "{" + response.content[0].text
            return FilterResult.model_validate_json(raw_text)

        return retry_with_backoff(
            _call,
            max_retries=MAX_RETRIES,
            base_delay=RETRY_BASE_DELAY,
            retryable=(
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ),
            context="anthropic",
        )

    return batch_fn


# ---------------------------------------------------------------------------
# Minimax provider (NEW)
# ---------------------------------------------------------------------------


def _make_minimax_batch_fn(config: dict, profile: str, constraints: str):
    api_key = config.get("minimax_api_key", "")
    if not api_key:
        raise RuntimeError("minimax_api_key not set in config")
    if len(api_key) < 20:
        raise RuntimeError(f"minimax_api_key seems invalid (too short): {api_key[:10]}...")
    
    model = config.get("minimax_model", "MiniMax-M2.5")
    
    def batch_fn(batch: list[dict]) -> FilterResult:
        return _filter_batch_minimax(batch, profile, constraints, api_key, model)

    return batch_fn


def _filter_batch_minimax(
    batch: list[dict],
    profile: str,
    constraints: str,
    api_key: str,
    model: str = "MiniMax-M2.5",
) -> FilterResult:
    """Call Minimax API and parse the JSON response."""
    jobs_text = _format_jobs_for_prompt(batch)
    system_prompt, user_prompt = _build_job_filter_prompt(
        profile, constraints, jobs_text, include_system_in_user=True
    )

    # Minimax API endpoint
    url = "https://api.minimax.io/v1/text/chatcompletion_v2"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    max_tokens = MAX_TOKENS
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=MINIMAX_TIMEOUT,
            )
            response.raise_for_status()

            data = response.json()

            # Extract content from Minimax response
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"]
            else:
                raise RuntimeError(f"Unexpected Minimax response: {data}")

            # Parse JSON from response (may be wrapped in markdown)
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            # Ensure we have valid JSON by adding braces if needed
            if not content.startswith("{"):
                # Find the first { and last }
                start = content.find("{")
                end = content.rfind("}")
                if start >= 0 and end > start:
                    content = content[start:end+1]

            return FilterResult.model_validate_json(content)
        except ValidationError as exc:
            # JSON parsing failed (truncated response) - retry with more tokens
            last_exc = exc
            if attempt < MAX_RETRIES:
                max_tokens *= 2
                logger.warning(
                    f"[minimax] Response truncated at max_tokens={max_tokens//2}, "
                    f"retrying with max_tokens={max_tokens}"
                )
                time.sleep(RETRY_BASE_DELAY * (attempt + 1))
                continue
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                continue
            raise

    # Should not reach here, but raise last exception if we did
    raise last_exc or RuntimeError("Unexpected exit from Minimax retry loop")


# ---------------------------------------------------------------------------
# opencode provider
# ---------------------------------------------------------------------------


def _make_opencode_batch_fn(config: dict, profile: str, constraints: str):
    model = config.get("opencode_model", "")
    opencode_exe = shutil.which("opencode")
    if not opencode_exe:
        raise RuntimeError(
            "opencode executable not found in PATH. "
            "Install it with: npm install -g opencode-ai"
        )

    def batch_fn(batch: list[dict]) -> FilterResult:
        return _filter_batch_opencode(
            batch, profile, constraints, model, opencode_exe
        )

    return batch_fn


def _filter_batch_opencode(
    batch: list[dict],
    profile: str,
    constraints: str,
    model: str,
    opencode_exe: str,
) -> FilterResult:
    """Call opencode CLI via a temp file and parse the JSON response."""
    jobs_text = _format_jobs_for_prompt(batch)
    _, prompt_content = _build_job_filter_prompt(
        profile, constraints, jobs_text, include_system_in_user=True
    )

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="jobhunter_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(prompt_content)

        cmd = [
            opencode_exe,
            "run",
            "Read the attached file carefully and respond with the JSON object exactly as instructed inside it.",
            "--file",
            tmp_path,
        ]
        if model:
            cmd += ["--model", model]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=OPENCODE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"opencode timed out after {OPENCODE_TIMEOUT}s")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if proc.returncode != 0:
        logger.warning("opencode stderr: %s", proc.stderr[:1000] or "(empty)")
        logger.warning("opencode stdout: %s", proc.stdout[:500] or "(empty)")
        raise RuntimeError(f"opencode exited with code {proc.returncode}")

    if not proc.stdout.strip():
        raise RuntimeError("opencode returned empty response")

    # Parse JSON from opencode output (may be wrapped in markdown)
    output = proc.stdout.strip()
    if output.startswith("```json"):
        output = output[7:]
    if output.startswith("```"):
        output = output[3:]
    if output.endswith("```"):
        output = output[:-3]
    output = output.strip()

    return FilterResult.model_validate_json(output)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_jobs_for_prompt(batch: list[dict]) -> str:
    """Format a batch of jobs into a text prompt."""
    lines = []
    for i, job in enumerate(batch):
        title = str(job.get("title", "Unknown"))[:100]
        company = str(job.get("company", "Unknown"))[:50]
        location = str(job.get("location", "Unknown"))[:50]
        description = str(job.get("description", ""))[:MAX_DESCRIPTION_CHARS]
        url = job.get("url", "")

        lines.append(
            f"--- Job {i} ---\n"
            f"Title: {title}\n"
            f"Company: {company}\n"
            f"Location: {location}\n"
            f"Description: {description}\n"
            f"URL: {url}\n"
        )

    return "\n".join(lines)


def _extract_approved(result: FilterResult, batch: list[dict]) -> list[dict]:
    """Extract approved jobs with their reasons and scores."""
    approved = []
    for item in result.approved:
        idx = item.job_index
        if 0 <= idx < len(batch):
            job = dict(batch[idx])
            job["match_reason"] = item.reason
            job["match_score"] = item.score if item.score else 0.0
            approved.append(job)
        else:
            logger.warning("Invalid job_index %d in filter result", idx)

    # Sort by score descending
    approved.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return approved
