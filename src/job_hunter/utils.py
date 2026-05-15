"""utils.py — Shared retry logic, constants, and helpers."""

import json
import logging
import os
import time
from functools import wraps
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Errors that should never be retried - they're fatal
NON_RETRYABLE_EXCEPTIONS = (
    ValueError,
    TypeError,
    KeyError,
    ImportError,
)

# Source color mapping for Discord embeds
SOURCE_COLORS = {
    "linkedin": 0x0A66C2,
    "indeed": 0x2164F3,
    "glassdoor": 0x0CAA41,
    "gupy": 0xFF6B35,
    "remoteok": 0x0DAFD4,
    "weworkremotely": 0x007BFF,
}
DEFAULT_SOURCE_COLOR = 0x666666


def source_color_hex(source: str) -> str:
    """Return a hex color string for the given job source (for HTML email)."""
    colors = {
        "linkedin": "#0a66c2",
        "indeed": "#2164f3",
        "glassdoor": "#0caa41",
        "gupy": "#ff6b35",
        "remoteok": "#0dafd4",
        "weworkremotely": "#007bff",
    }
    return colors.get(source.lower(), "#666666")


def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 5.0,
    retryable: tuple[type[Exception], ...] = (requests.RequestException,),
    context: str = "",
) -> any:
    """Retry *func* with exponential backoff.

    Args:
        func: Callable to execute.
        max_retries: Maximum number of attempts (0 = no retry).
        base_delay: Initial delay in seconds; doubles each retry.
        retryable: Tuple of exception types to retry on. Defaults to network errors only.
        context: Optional description for logging.

    Returns:
        The result of *func* on success.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            return func()
        except retryable as exc:
            # Fail immediately on non-retryable exceptions (ValueError, TypeError, etc.)
            if isinstance(exc, NON_RETRYABLE_EXCEPTIONS):
                logger.error(
                    "[%s] Non-retryable error (skipping retries): %s", context, exc
                )
                raise exc from None

            last_exc = exc
            if attempt < max_retries:
                logger.warning(
                    "[%s] Attempt %d/%d failed: %s — retrying in %.1fs",
                    context,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay *= 2
            else:
                logger.error(
                    "[%s] All %d attempts failed: %s",
                    context,
                    max_retries + 1,
                    exc,
                )

    raise last_exc from None


def atomic_write_json(path: Path, data: dict) -> None:
    """Write *data* as JSON atomically: write to .tmp file, then rename.

    The os.replace rename is atomic on both POSIX and Windows,
    preventing partial-file corruption if the process is killed mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
