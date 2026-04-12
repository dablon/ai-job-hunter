"""researcher.py — AI-powered job preparation research.

For each approved job, this module generates a "Preparation Guide" block that explains:
- Recommended certifications or courses
- Key skills to highlight
- Typical interview process
- Salary negotiation tips
- Any other actionable advice
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger(__name__)

RESEARCH_BATCH_SIZE = 5  # Jobs per API call
RESEARCH_DELAY_SECONDS = 2  # Delay between batches
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5
MAX_TITLE_CHARS = 150
MAX_DESCRIPTION_CHARS = 2000


RESEARCH_PROMPT_TEMPLATE = """You are a career preparation advisor. For the job posting below, provide actionable advice on how to prepare for this role.

Respond with ONLY a valid JSON object (no markdown, no explanation outside the JSON):

{{
  "preparation_tips": [
    "Specific certification or course recommendation",
    "Key skill to highlight based on the description",
    "Interview topic to prepare",
    "Salary negotiation advice"
  ],
  "recommended_certifications": [
    "Certification name that is valued for this role"
  ],
  "interview_process": "Brief description of typical interview rounds for this type of role",
  "key_responsibilities": ["Main responsibility 1", "Main responsibility 2"],
  "career_path": "Brief note on typical career progression for someone in this role"
}}

## Job Posting
Title: {title}
Company: {company}
Description: {description}

Provide practical, specific advice. Focus on certifications and skills that are widely recognized in the industry. If the job is in a specialized field, mention domain-specific certifications.

Respond with ONLY the JSON object."""

SYSTEM_PROMPT = (
    "You are a helpful career preparation advisor. "
    "Respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON. "
    "Always include all fields: preparation_tips, recommended_certifications, "
    "interview_process, key_responsibilities, career_path."
)


def _build_research_prompt(job: dict) -> str:
    """Build the research prompt for a single job."""
    title = str(job.get("title", ""))[:MAX_TITLE_CHARS]
    company = str(job.get("company", ""))[:100]
    description = str(job.get("description", ""))[:MAX_DESCRIPTION_CHARS]

    return RESEARCH_PROMPT_TEMPLATE.format(
        title=title,
        company=company,
        description=description,
    )


def _parse_research_response(content: str) -> dict:
    """Parse the JSON response from the AI."""
    content = content.strip()

    # Strip markdown code fences
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    # Try to find JSON object if there's extra text
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end + 1]

    try:
        result = json.loads(content)
        # Validate required fields
        required = ["preparation_tips", "recommended_certifications", "interview_process",
                    "key_responsibilities", "career_path"]
        for field in required:
            if field not in result:
                result[field] = ""
        return result
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse research JSON: %s. Content: %s...", e, content[:200])
        return {
            "preparation_tips": ["Research the company and role thoroughly"],
            "recommended_certifications": [],
            "interview_process": "Check the job description for details",
            "key_responsibilities": [],
            "career_path": "",
        }


def _research_job_minimax(job: dict, config: dict) -> dict:
    """Research a single job using Minimax API."""
    api_key = config.get("minimax_api_key", "")
    model = config.get("minimax_model", "MiniMax-M2.5")
    url = "https://api.minimax.io/v1/text/chatcompletion_v2"

    prompt = _build_research_prompt(job)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=90)
            response.raise_for_status()
            data = response.json()

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise RuntimeError("Empty response from Minimax")

            result = _parse_research_response(content)
            logger.info("Researched job: %s @ %s", job.get("title", "")[:50], job.get("company", ""))
            return result

        except Exception as e:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning("Minimax research attempt %d/%d failed for '%s': %s. Retrying in %ds...",
                          attempt + 1, MAX_RETRIES, job.get("title", "")[:30], e, delay)
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)

    # Return fallback on all retries exhausted
    return {
        "preparation_tips": ["Research the company and role thoroughly"],
        "recommended_certifications": [],
        "interview_process": "Check the job description for details",
        "key_responsibilities": [],
        "career_path": "",
    }


def _research_job_anthropic(job: dict, config: dict) -> dict:
    """Research a single job using Anthropic API."""
    import anthropic

    api_key = config.get("anthropic_api_key", "")
    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_research_prompt(job)

    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            content = message.content[0].text
            result = _parse_research_response(content)
            logger.info("Researched job: %s @ %s", job.get("title", "")[:50], job.get("company", ""))
            return result

        except Exception as e:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning("Anthropic research attempt %d/%d failed for '%s': %s. Retrying in %ds...",
                          attempt + 1, MAX_RETRIES, job.get("title", "")[:30], e, delay)
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)

    return {
        "preparation_tips": ["Research the company and role thoroughly"],
        "recommended_certifications": [],
        "interview_process": "Check the job description for details",
        "key_responsibilities": [],
        "career_path": "",
    }


def research_jobs(
    jobs: list[dict],
    config: dict,
    provider: str = "minimax",
    parallel: bool = True,
) -> list[dict]:
    """Research preparation info for all approved jobs.

    Each job dict will be enriched with a 'preparation_guide' field containing:
    - preparation_tips: list of actionable tips
    - recommended_certifications: list of cert names
    - interview_process: string description
    - key_responsibilities: list of main responsibilities
    - career_path: string about career progression

    Args:
        jobs: List of job dicts (from filter output)
        config: Full config dict with API keys
        provider: AI provider - 'minimax' or 'anthropic'
        parallel: If True, research jobs in parallel (default). If False, sequential.

    Returns:
        Jobs list with each job enriched with 'preparation_guide' field.
    """
    if not jobs:
        return jobs

    if not config.get("minimax_api_key") and not config.get("anthropic_api_key"):
        logger.warning("No AI API key configured — skipping job research")
        return jobs

    research_fn = _research_job_minimax if provider == "minimax" else _research_job_anthropic

    # Deduplicate by title+company to avoid redundant API calls
    seen: set[tuple[str, str]] = set()
    unique_jobs: list[dict] = []
    for job in jobs:
        title = str(job.get("title", "") or "")[:50]
        company = str(job.get("company", "") or "")[:50]
        key = (title, company)
        if key not in seen:
            seen.add(key)
            unique_jobs.append(job)

    if len(unique_jobs) < len(jobs):
        logger.info("Deduplicated %d jobs to %d unique titles for research", len(jobs), len(unique_jobs))

    enriched_jobs: list[dict] = []

    if parallel:
        # Parallel research using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(unique_jobs))) as executor:
            future_to_job = {
                executor.submit(research_fn, job, config): job
                for job in unique_jobs
            }

            for future in as_completed(future_to_job):
                job = future_to_job[future]
                try:
                    research_data = future.result()
                    job = dict(job)
                    job["preparation_guide"] = research_data
                    enriched_jobs.append(job)
                except Exception:
                    logger.exception("Research failed for job: %s", str(job.get("title", "") or "")[:30])
                    job = dict(job)
                    job["preparation_guide"] = {
                        "preparation_tips": ["Research the company and role thoroughly"],
                        "recommended_certifications": [],
                        "interview_process": "Check the job description for details",
                        "key_responsibilities": [],
                        "career_path": "",
                    }
                    enriched_jobs.append(job)

        # Maintain original order
        def _job_key(j: dict) -> tuple[str, str]:
            return (str(j.get("title", "") or "")[:50], str(j.get("company", "") or "")[:50])

        enriched_map = {_job_key(j): j for j in enriched_jobs}
        enriched_jobs = [
            enriched_map.get(_job_key(j), j)
            for j in unique_jobs
        ]
    else:
        # Sequential research
        for i, job in enumerate(unique_jobs):
            logger.info("Researching job %d/%d: %s", i + 1, len(unique_jobs),
                       str(job.get("title", "") or "")[:50])
            research_data = research_fn(job, config)
            job = dict(job)
            job["preparation_guide"] = research_data
            enriched_jobs.append(job)

            if i < len(unique_jobs) - 1:
                time.sleep(RESEARCH_DELAY_SECONDS)

    logger.info("Research complete for %d jobs", len(enriched_jobs))
    return enriched_jobs


def format_preparation_guide_for_display(prep: dict) -> str:
    """Format a preparation guide dict into a readable multi-line string."""
    lines = []

    certs = prep.get("recommended_certifications", [])
    if certs:
        lines.append("Recommended Certifications:")
        for cert in certs:
            lines.append(f"  • {cert}")
        lines.append("")

    tips = prep.get("preparation_tips", [])
    if tips:
        lines.append("Preparation Tips:")
        for tip in tips:
            lines.append(f"  • {tip}")
        lines.append("")

    interview = prep.get("interview_process", "")
    if interview:
        lines.append(f"Interview Process: {interview}")
        lines.append("")

    responsibilities = prep.get("key_responsibilities", [])
    if responsibilities:
        lines.append("Key Responsibilities:")
        for resp in responsibilities[:5]:  # Limit to 5
            lines.append(f"  • {resp}")
        lines.append("")

    career = prep.get("career_path", "")
    if career:
        lines.append(f"Career Path: {career}")

    return "\n".join(lines) if lines else ""


def format_preparation_guide_html(prep: dict) -> str:
    """Format a preparation guide dict into an HTML block for emails."""
    sections = []

    certs = prep.get("recommended_certifications", [])
    if certs:
        certs_html = "".join(
            f'<span style="display:inline-block;background:#e8f4ea;color:#1a8917;'
            f'padding:3px 8px;border-radius:4px;font-size:12px;margin:2px;">{c}</span>'
            for c in certs
        )
        sections.append(
            f'<p style="margin:6px 0 4px 0;font-size:13px;color:#333;">'
            f'<strong>Certifications:</strong><br>{certs_html}</p>'
        )

    tips = prep.get("preparation_tips", [])
    if tips:
        tips_html = "".join(
            f'<li style="margin:3px 0 3px 16px;">{t}</li>'
            for t in tips
        )
        sections.append(
            f'<p style="margin:6px 0 4px 0;font-size:13px;color:#333;"><strong>Preparation Tips:</strong></p>'
            f'<ul style="margin:4px 0 8px 0;padding-left:20px;color:#555;font-size:13px;">{tips_html}</ul>'
        )

    interview = prep.get("interview_process", "")
    if interview:
        sections.append(
            f'<p style="margin:4px 0;font-size:13px;color:#333;"><strong>Interview Process:</strong> {interview}</p>'
        )

    responsibilities = prep.get("key_responsibilities", [])
    if responsibilities:
        resp_html = "".join(
            f'<li style="margin:2px 0 2px 16px;">{r}</li>'
            for r in responsibilities[:5]
        )
        sections.append(
            f'<p style="margin:6px 0 4px 0;font-size:13px;color:#333;"><strong>Key Responsibilities:</strong></p>'
            f'<ul style="margin:4px 0 8px 0;padding-left:20px;color:#555;font-size:13px;">{resp_html}</ul>'
        )

    career = prep.get("career_path", "")
    if career:
        sections.append(
            f'<p style="margin:4px 0;font-size:13px;color:#555;font-style:italic;"><strong>Career Path:</strong> {career}</p>'
        )

    if not sections:
        return ""

    return (
        f'<div style="margin-top:12px;padding:12px;background:#f9f9f9;'
        f'border-radius:6px;border-left:3px solid #28a745;">'
        f'<p style="margin:0 0 8px 0;font-size:14px;color:#1a1a1a;font-weight:bold;">'
        f'Preparation Guide</p>'
        + "".join(sections) +
        f'</div>'
    )
