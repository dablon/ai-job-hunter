"""ats.py — Application Tracking System for job applications.

This module provides functionality to track job applications, including:
- Application status (applied, interview, rejected, offer)
- Notes and follow-ups
- Company information
- Interview timeline
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ATS_DATA_PATH = Path("config/ats_data.json")

# Application statuses
class ApplicationStatus:
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"

VALID_STATUSES = {
    ApplicationStatus.SAVED,
    ApplicationStatus.APPLIED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.REJECTED,
    ApplicationStatus.WITHDRAWN,
    ApplicationStatus.ARCHIVED,
}


def _load_ats_data() -> dict:
    """Load ATS data from file."""
    if not ATS_DATA_PATH.exists():
        return {"applications": [], "settings": {}}

    try:
        with open(ATS_DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load ATS data: %s", e)
        return {"applications": [], "settings": {}}


def _save_ats_data(data: dict) -> None:
    """Save ATS data to file."""
    try:
        ATS_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ATS_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error("Failed to save ATS data: %s", e)


def get_all_applications() -> list[dict]:
    """Get all tracked applications."""
    data = _load_ats_data()
    return data.get("applications", [])


def get_application_by_url(url: str) -> Optional[dict]:
    """Find an application by job URL."""
    data = _load_ats_data()
    for app in data.get("applications", []):
        if app.get("url") == url:
            return app
    return None


def add_application(
    job: dict,
    status: str = ApplicationStatus.SAVED,
    notes: str = "",
) -> dict:
    """Add a new job application to tracking.

    Args:
        job: Job dictionary with title, company, url, etc.
        status: Initial application status
        notes: Optional notes

    Returns:
        The created application dict
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Valid: {VALID_STATUSES}")

    data = _load_ats_data()

    # Check if already exists
    for app in data.get("applications", []):
        if app.get("url") == job.get("url"):
            logger.info("Application already exists: %s", job.get("url"))
            return app

    application = {
        "id": len(data.get("applications", [])) + 1,
        "url": job.get("url", ""),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "source": job.get("source", ""),
        "salary": job.get("salary", ""),
        "status": status,
        "notes": notes,
        "applied_date": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "timeline": [
            {
                "status": status,
                "date": datetime.now().isoformat(),
                "notes": "Application added",
            }
        ],
    }

    if "applications" not in data:
        data["applications"] = []

    data["applications"].append(application)
    _save_ats_data(data)

    logger.info("Added application: %s at %s", application["title"], application["company"])
    return application


def update_application_status(
    url: str,
    new_status: str,
    notes: str = "",
) -> Optional[dict]:
    """Update the status of an application.

    Args:
        url: Job URL
        new_status: New status
        notes: Optional notes about the change

    Returns:
        Updated application dict or None if not found
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")

    data = _load_ats_data()

    for app in data.get("applications", []):
        if app.get("url") == url:
            old_status = app.get("status")
            app["status"] = new_status
            app["updated_at"] = datetime.now().isoformat()

            # Set applied_date when first marked as applied
            if new_status == ApplicationStatus.APPLIED and not app.get("applied_date"):
                app["applied_date"] = datetime.now().isoformat()

            # Add to timeline
            timeline_entry = {
                "status": new_status,
                "date": datetime.now().isoformat(),
                "notes": notes or f"Status changed from {old_status} to {new_status}",
            }

            if "timeline" not in app:
                app["timeline"] = []

            app["timeline"].append(timeline_entry)

            if notes:
                app["notes"] = notes

            _save_ats_data(data)
            logger.info("Updated application %s: %s -> %s", url, old_status, new_status)
            return app

    logger.warning("Application not found: %s", url)
    return None


def add_note(url: str, note: str) -> Optional[dict]:
    """Add a note to an application.

    Args:
        url: Job URL
        note: Note text

    Returns:
        Updated application dict or None if not found
    """
    data = _load_ats_data()

    for app in data.get("applications", []):
        if app.get("url") == url:
            # Append to existing notes
            existing_notes = app.get("notes", "")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_note = f"[{timestamp}] {note}"

            if existing_notes:
                app["notes"] = f"{existing_notes}\n{new_note}"
            else:
                app["notes"] = new_note

            app["updated_at"] = datetime.now().isoformat()

            # Add to timeline
            timeline_entry = {
                "status": app["status"],
                "date": datetime.now().isoformat(),
                "notes": note,
            }

            if "timeline" not in app:
                app["timeline"] = []

            app["timeline"].append(timeline_entry)

            _save_ats_data(data)
            logger.info("Added note to application: %s", url)
            return app

    return None


def delete_application(url: str) -> bool:
    """Delete an application.

    Args:
        url: Job URL

    Returns:
        True if deleted, False if not found
    """
    data = _load_ats_data()

    original_count = len(data.get("applications", []))
    data["applications"] = [
        app for app in data.get("applications", []) if app.get("url") != url
    ]

    if len(data["applications"]) < original_count:
        _save_ats_data(data)
        logger.info("Deleted application: %s", url)
        return True

    return False


def get_applications_by_status(status: str) -> list[dict]:
    """Get all applications with a specific status."""
    data = _load_ats_data()
    return [app for app in data.get("applications", []) if app.get("status") == status]


def get_statistics() -> dict:
    """Get application statistics.

    Returns:
        Dict with counts by status and other metrics
    """
    data = _load_ats_data()
    applications = data.get("applications", [])

    stats = {
        "total": len(applications),
        "by_status": {},
        "response_rate": 0.0,
        "interview_rate": 0.0,
    }

    # Count by status
    for app in applications:
        status = app.get("status", "unknown")
        stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

    # Calculate rates
    applied_count = sum(
        1 for app in applications if app.get("status") != ApplicationStatus.SAVED
    )
    if applied_count > 0:
        interview_count = sum(
            1
            for app in applications
            if app.get("status") in {ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER}
        )
        stats["response_rate"] = round(
            (interview_count / applied_count) * 100, 1
        )

    return stats


def import_from_sent_jobs(sent_jobs: list[dict]) -> int:
    """Import jobs from sent jobs list as saved applications.

    This is useful to start tracking jobs that were previously sent
    but not tracked in ATS.

    Args:
        sent_jobs: List of job dictionaries

    Returns:
        Number of jobs imported
    """
    imported = 0

    for job in sent_jobs:
        try:
            add_application(job, status=ApplicationStatus.SAVED)
            imported += 1
        except Exception:
            logger.warning("Failed to import job: %s", job.get("url"))

    return imported
