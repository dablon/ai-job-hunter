"""linkedin_analyzer.py — LinkedIn profile scraping and AI-powered config generation."""

import json
import logging
import re
import sys
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SCRAPING_TIMEOUT = 30  # seconds
DEFAULT_CONFIG_PATH = Path("config.json")

# Fix UTF-8 encoding for Windows console
if sys.platform == "win32":
    try:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    except Exception:
        pass


def scrape_linkedin_profile(url: str) -> dict:
    """Scrape a LinkedIn profile using Playwright.

    Args:
        url: LinkedIn profile URL

    Returns:
        Dictionary with profile data: headline, summary, experience, skills, etc.

    Raises:
        RuntimeError: If scraping fails
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            f"Playwright not available: {e}. "
            "Install with: pip install playwright && playwright install chromium"
        )

    # Validate URL
    if not url.startswith("https://www.linkedin.com/in/"):
        raise RuntimeError(f"Invalid LinkedIn profile URL: {url}")

    profile_data = {
        "url": url,
        "headline": "",
        "summary": "",
        "experience": [],
        "education": [],
        "skills": [],
        "locations": [],
    }

    try:
        with sync_playwright() as p:
            # Launch headless browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = context.new_page()

            # Navigate to profile
            logger.info(f"Scraping LinkedIn profile: {url}")
            page.goto(url, timeout=SCRAPING_TIMEOUT * 1000, wait_until="domcontentloaded")

            # Wait for profile content to load
            page.wait_for_timeout(3000)

            # Extract headline
            try:
                headline_elem = page.locator(".text-body-medium").first
                profile_data["headline"] = headline_elem.text_content(timeout=5000).strip()
            except Exception:
                pass

            # Extract summary/about section
            try:
                # Try multiple selectors for summary
                summary_selectors = [
                    "#about ~ .pvs-list__container",
                    ".profile-section-card__content",
                    "[data-section='about']",
                ]
                for selector in summary_selectors:
                    try:
                        summary_elem = page.locator(selector).first
                        if summary_elem.count() > 0:
                            profile_data["summary"] = summary_elem.text_content(timeout=3000).strip()
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            # Extract experience
            try:
                experience_section = page.locator('[data-section="experience"] ~ .pvs-list__container')
                if experience_section.count() > 0:
                    exp_items = experience_section.locator(".pvs-list__paged-list-item")
                    for i in range(min(exp_items.count(), 10)):  # Limit to 10
                        try:
                            exp_text = exp_items.nth(i).text_content(timeout=2000)
                            if exp_text:
                                profile_data["experience"].append(exp_text.strip())
                        except Exception:
                            continue
            except Exception:
                pass

            # Extract skills
            try:
                skills_section = page.locator('[data-section="skills"] ~ .pvs-list__container')
                if skills_section.count() > 0:
                    skill_items = skills_section.locator(".pvs-list__paged-list-item")
                    for i in range(min(skill_items.count(), 20)):  # Limit to 20
                        try:
                            skill_text = skill_items.nth(i).text_content(timeout=2000)
                            if skill_text:
                                # Clean up skill text
                                skill_name = skill_text.split("\n")[0].strip()
                                if skill_name:
                                    profile_data["skills"].append(skill_name)
                        except Exception:
                            continue
            except Exception:
                pass

            # Extract location
            try:
                location_elem = page.locator(".text-body-small").first
                location_text = location_elem.text_content(timeout=3000).strip()
                if location_text and any(word in location_text.lower() for word in ["city", "area", "region"]):
                    profile_data["locations"].append(location_text)
            except Exception:
                pass

            browser.close()

        logger.info(f"Scraped profile: {profile_data.get('headline', 'N/A')}")
        return profile_data

    except Exception as e:
        logger.error(f"Failed to scrape LinkedIn profile: {e}")
        raise RuntimeError(f"LinkedIn scraping failed: {e}")


def scrape_linkedin_fallback(url: str, profile_text: str = None) -> dict:
    """Fallback method to get LinkedIn profile data.

    If Playwright fails, this allows manual profile input.

    Args:
        url: LinkedIn profile URL
        profile_text: Optional profile text to use instead of scraping

    Returns:
        Dictionary with profile data
    """
    profile_data = {
        "url": url,
        "headline": "",
        "summary": profile_text or "",
        "experience": [],
        "education": [],
        "skills": [],
        "locations": [],
    }

    if profile_text:
        # Try to extract skills from the text
        # This is a simple heuristic - AI will do the full analysis
        logger.info("Using provided profile text for analysis")

    return profile_data


def analyze_profile_with_ai(profile_data: dict, config: dict, provider: str = "minimax") -> dict:
    """Use AI to analyze profile data and generate config suggestions.

    Args:
        profile_data: Scraped LinkedIn profile data
        config: Existing config (for API keys)
        provider: AI provider (minimax or anthropic)

    Returns:
        Dictionary with AI analysis: keywords, search_tips, refined_profile
    """
    if provider == "minimax":
        return _analyze_profile_minimax(profile_data, config)
    else:
        return _analyze_profile_anthropic(profile_data, config)


def _analyze_profile_minimax(profile_data: dict, config: dict) -> dict:
    """Use Minimax API to analyze profile."""
    api_key = config.get("minimax_api_key", "")
    model = config.get("minimax_model", "MiniMax-M2.5")

    if not api_key:
        raise RuntimeError("minimax_api_key not configured")

    # Build profile text from scraped data
    profile_text = _build_profile_text(profile_data)

    prompt = f"""Analyze this LinkedIn profile and generate an optimized job search configuration.

## LinkedIn Profile Data:
{profile_text}

Based on this profile, generate a job search configuration in JSON format:
{{
  "refined_profile": "2-3 sentence summary of the candidate's expertise and target roles",
  "suggested_keywords": ["keyword1", "keyword2", ...],
  "recommended_locations": ["location1", "location2", ...],
  "search_tips": "2-3 specific tips for finding matching jobs",
  "target_seniority": "junior/mid/senior/lead/principal",
  "relevant_industries": ["industry1", "industry2", ...]
}}

Focus on:
- Tech stack and specific technologies
- Years of experience and seniority level
- Industry background
- Specific role titles that match their experience
- Remote-friendly locations"""

    url = "https://api.minimax.io/v1/text/chatcompletion_v2"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a career advisor and job search expert. Analyze LinkedIn profiles and suggest optimized job search strategies."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Parse JSON from response
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Find JSON in response
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end+1]

        result = json.loads(content)
        logger.info(f"AI Analysis complete: {result.get('suggested_keywords', [])[:5]}...")
        return result

    except Exception as e:
        logger.error(f"Minimax analysis failed: {e}")
        raise RuntimeError(f"AI profile analysis failed: {e}")


def _analyze_profile_anthropic(profile_data: dict, config: dict) -> dict:
    """Use Anthropic API to analyze profile."""
    import anthropic

    api_key = config.get("anthropic_api_key", "")
    if not api_key:
        raise RuntimeError("anthropic_api_key not configured")

    client = anthropic.Anthropic(api_key=api_key)

    profile_text = _build_profile_text(profile_data)

    prompt = f"""Analyze this LinkedIn profile and generate an optimized job search configuration.

## LinkedIn Profile Data:
{profile_text}

Generate JSON with:
{{
  "refined_profile": "2-3 sentence summary",
  "suggested_keywords": ["keyword1", "keyword2", ...],
  "recommended_locations": ["location1", ...],
  "search_tips": "tips",
  "target_seniority": "seniority level",
  "relevant_industries": ["industry1", ...]
}}"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        content = message.content[0].text

        # Parse JSON
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end+1]

        result = json.loads(content)
        return result

    except Exception as e:
        logger.error(f"Anthropic analysis failed: {e}")
        raise RuntimeError(f"AI profile analysis failed: {e}")


def _build_profile_text(profile_data: dict) -> str:
    """Build a text representation of the profile for AI analysis."""
    parts = []

    if profile_data.get("headline"):
        parts.append(f"Headline: {profile_data['headline']}")

    if profile_data.get("summary"):
        parts.append(f"\nSummary:\n{profile_data['summary']}")

    if profile_data.get("experience"):
        parts.append(f"\nExperience:\n" + "\n".join(f"- {exp}" for exp in profile_data["experience"][:5]))

    if profile_data.get("skills"):
        parts.append(f"\nSkills: {', '.join(profile_data['skills'])}")

    if profile_data.get("locations"):
        parts.append(f"\nLocations: {', '.join(profile_data['locations'])}")

    return "\n".join(parts) or "No profile data available"


def generate_config_from_profile(profile_data: dict, analysis: dict) -> dict:
    """Generate a complete config.json from profile data and AI analysis.

    Args:
        profile_data: Raw scraped profile data
        analysis: AI-generated analysis

    Returns:
        Complete config dictionary ready to be saved as config.json
    """
    config = {
        "profile": analysis.get("refined_profile", profile_data.get("summary", "")),
        "keywords": analysis.get("suggested_keywords", []),
        "locations": analysis.get("recommended_locations", ["Remote"]),
        "location": "Remote",
        "remote_only": True,
        "filter_strictness": "loose",
        "exclude_companies": [
            "Toptal",
            "Crossover",
            "Turing",
            "Upwork",
            "Fiverr"
        ],
        "exclude_keywords": [
            "junior",
            "internship",
            "entry level"
        ],
        "salary_min_usd": 50000,
        "salary_max_usd": 200000,
        "_linkedin_analysis": {
            "source_url": profile_data.get("url", ""),
            "original_headline": profile_data.get("headline", ""),
            "target_seniority": analysis.get("target_seniority", ""),
            "relevant_industries": analysis.get("relevant_industries", []),
            "search_tips": analysis.get("search_tips", ""),
        }
    }

    return config


def save_config(config: dict, path: Path = None) -> Path:
    """Save config to file.

    Args:
        config: Config dictionary
        path: Output path (default: config.json in current dir)

    Returns:
        Path to saved file
    """
    if path is None:
        path = DEFAULT_CONFIG_PATH

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    logger.info(f"Config saved to {path}")
    return path


def analyze_linkedin_profile(url: str, config: dict, provider: str = "minimax", profile_text: str = None) -> dict:
    """Main entry point: scrape profile, analyze with AI, generate config.

    Args:
        url: LinkedIn profile URL
        config: Existing config (for API keys, etc.)
        provider: AI provider to use
        profile_text: Optional profile text if scraping fails

    Returns:
        Generated config dictionary
    """
    profile_data = None

    # Step 1: Try to scrape profile
    logger.info(f"Step 1/3: Scraping LinkedIn profile...")
    try:
        profile_data = scrape_linkedin_profile(url)
    except Exception as e:
        logger.warning(f"Scraping failed: {e}")
        if profile_text:
            logger.info("Using provided profile text instead")
            profile_data = scrape_linkedin_fallback(url, profile_text)
        else:
            # Provide helpful error message
            raise RuntimeError(
                f"LinkedIn scraping failed: {e}\n\n"
                "Options:\n"
                "1. Install Playwright: pip install playwright && playwright install chromium\n"
                "2. Use --profile-text to provide your profile manually\n"
                "3. Use LinkedIn's 'Export to PDF' feature and paste the content"
            )

    # Step 2: AI analysis
    logger.info(f"Step 2/3: Analyzing profile with AI ({provider})...")
    analysis = analyze_profile_with_ai(profile_data, config, provider)

    # Step 3: Generate config
    logger.info(f"Step 3/3: Generating config.json...")
    final_config = generate_config_from_profile(profile_data, analysis)

    return final_config
