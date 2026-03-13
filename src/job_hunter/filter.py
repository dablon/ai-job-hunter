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
from pydantic import BaseModel

from job_hunter.utils import retry_with_backoff

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-haiku-4-5"
MINIMAX_MODEL = "MiniMax-M2.5"
BATCH_SIZE = 25
MAX_TOKENS = 4096
MAX_DESCRIPTION_CHARS = 1500
BATCH_DELAY_SECONDS = 2
OPENCODE_TIMEOUT = 180  # seconds per batch subprocess call
MINIMAX_TIMEOUT = 60  # seconds per API call
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds — doubles each retry (5, 10, 20)


class ApprovedJob(BaseModel):
    job_index: int
    reason: str


class FilterResult(BaseModel):
    approved: list[ApprovedJob]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def filter_jobs(
    jobs: list[dict], config: dict, provider: str = "anthropic"
) -> list[dict]:
    """Filter jobs using the specified AI provider.

    Returns approved jobs, each enriched with a 'reason' field.
    Raises RuntimeError if all batches fail or a permanent error occurs.
    """
    if not jobs:
        logger.info("No jobs to filter")
        return []

    profile = config.get("profile", "")
    constraints = _build_hard_constraints(config)

    if provider == "opencode":
        batch_fn = _make_opencode_batch_fn(config, profile, constraints)
        return _filter_in_batches(jobs, batch_fn, provider)
    elif provider == "minimax":
        batch_fn = _make_minimax_batch_fn(config, profile, constraints)
        return _filter_in_batches(jobs, batch_fn, provider)
    else:
        batch_fn = _make_anthropic_batch_fn(config, profile, constraints)
        return _filter_in_batches(
            jobs, batch_fn, provider, abort_on=(anthropic.BadRequestError,)
        )


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
            "- REMOTE WORK REQUIRED: reject ANY job that is on-site, "
            "hybrid, or requires physical presence. "
            "Only approve if the job explicitly offers 100% remote work. "
            "When in doubt, REJECT."
        )

    location = config.get("location", "").strip()
    if location and not config.get("remote_only"):
        lines.append(
            f"- LOCATION: the user is in {location}. "
            f"Reject on-site jobs outside {location}."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a strict job-matching assistant. Respond ONLY with a JSON object — "
    "no markdown, no explanation, no text before or after the JSON.\n\n"
    "APPROVE a job ONLY if the user clearly meets ALL of its stated requirements.\n"
    "When in doubt, REJECT.\n\n"
    "REJECT jobs that:\n"
    "- The user lacks any required skill, technology, or experience listed in the posting\n"
    "- Are sponsored/promoted listings or reposts with minimal content\n"
    "- Have wrong seniority level (e.g. requires senior when user is mid-level)\n"
    "- Require a tech stack the user does not have\n"
    "- Require on-site presence, are hybrid, or do not explicitly offer remote work\n"
    "- Are in the wrong language or require relocation\n"
    "- Are in a location the user does not live except for remote positions, "
    "or if the user explicitly said they are open to relocation\n"
    "The 'reason' field must explain only WHY the job IS a good match. "
    "Never mention missing skills or caveats in reason — if there are any, REJECT instead.\n\n"
    'Required format: {"approved": [{"job_index": 0, "reason": "reason in Portuguese"}]}\n'
    'If nothing matches: {"approved": []}'
)


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

    def batch_fn(batch: list[dict]) -> FilterResult:
        return _filter_batch_minimax(batch, profile, constraints, api_key)

    return batch_fn


def _filter_batch_minimax(
    batch: list[dict],
    profile: str,
    constraints: str,
    api_key: str,
) -> FilterResult:
    """Call Minimax API and parse the JSON response."""
    jobs_text = _format_jobs_for_prompt(batch)

    hard_constraints_section = (
        f"\n\nHARD CONSTRAINTS (non-negotiable — REJECT if violated):\n{constraints}\n"
        if constraints
        else ""
    )

    # Build prompt for Minimax
    user_prompt = (
        "IMPORTANT: Your response must be ONLY a valid JSON object. "
        "No markdown, no explanation, no text before or after the JSON.\n\n"
        "Task: Evaluate the job postings below against the user's profile. "
        "APPROVE a job ONLY if the user clearly meets ALL of its stated requirements. "
        "When in doubt, REJECT.\n\n"
        "REJECT jobs that:\n"
        "- The user lacks any required skill, technology, or experience listed in the posting\n"
        "- Are sponsored/promoted listings or reposts with minimal content\n"
        "- Have wrong seniority level or wrong tech stack\n"
        "- Require on-site presence, are hybrid, or do not explicitly offer remote work\n"
        "- Are in the wrong language or require relocation\n\n"
        "The 'reason' field must explain only WHY the job IS a good match. "
        "Never mention missing skills or caveats in reason — if there are any, REJECT instead.\n\n"
        "Required JSON format:\n"
        '{"approved": [{"job_index": 0, "reason": "reason in English or Spanish"}, ...]}\n'
        'If nothing matches: {"approved": []}\n\n'
        f"## User Profile\n{profile}"
        f"{hard_constraints_section}\n\n"
        f"## Jobs to Evaluate\n{jobs_text}\n\n"
        "Respond with ONLY the JSON object."
    )

    # Minimax API endpoint
    url = "https://api.minimax.chat/v1/text/chatcompletion_pro"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.2,
    }

    def _call() -> FilterResult:
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

    return retry_with_backoff(
        _call,
        max_retries=MAX_RETRIES,
        base_delay=RETRY_BASE_DELAY,
        retryable=(requests.exceptions.RequestException,),
        context="minimax",
    )


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

    hard_constraints_section = (
        f"\n\nHARD CONSTRAINTS (non-negotiable — REJECT if violated):\n{constraints}\n"
        if constraints
        else ""
    )

    prompt_content = (
        "IMPORTANT: Your response must be ONLY a valid JSON object. "
        "No markdown, no explanation, no text before or after the JSON.\n\n"
        "Task: Evaluate the job postings below against the user's profile. "
        "APPROVE a job ONLY if the user clearly meets ALL of its stated requirements. "
        "When in doubt, REJECT.\n\n"
        "REJECT jobs that:\n"
        "- The user lacks any required skill, technology, or experience listed in the posting\n"
        "- Are sponsored/promoted listings or reposts with minimal content\n"
        "- Have wrong seniority level or wrong tech stack\n"
        "- Require on-site presence, are hybrid, or do not explicitly offer remote work\n"
        "- Are in the wrong language or require relocation\n\n"
        "The 'reason' field must explain only WHY the job IS a good match. "
        "Never mention missing skills or caveats in reason — if there are any, REJECT instead.\n\n"
        "Required JSON format:\n"
        '{"approved": [{"job_index": 0, "reason": "reason in Portuguese"}, ...]}\n'
        'If nothing matches: {"approved": []}\n\n'
        f"## User Profile\n{profile}"
        f"{hard_constraints_section}\n\n"
        f"## Jobs to Evaluate\n{jobs_text}\n\n"
        "Respond with ONLY the JSON object."
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
        title = job.get("title", "Unknown")[:100]
        company = job.get("company_name", "Unknown")[:50]
        location = job.get("location", "Unknown")[:50]
        description = job.get("description", "")[:MAX_DESCRIPTION_CHARS]
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
    """Extract approved jobs with their reasons."""
    approved = []
    for item in result.approved:
        idx = item.job_index
        if 0 <= idx < len(batch):
            job = dict(batch[idx])
            job["match_reason"] = item.reason
            approved.append(job)
        else:
            logger.warning("Invalid job_index %d in filter result", idx)
    return approved
