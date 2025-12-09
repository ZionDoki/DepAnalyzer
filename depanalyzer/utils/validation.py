"""Input validation utilities for security and correctness."""

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("depanalyzer.utils.validation")

# Allowed URL schemes
ALLOWED_SCHEMES = {"http", "https", "git", "ssh", "ftp", "ftps"}

def validate_url(url: str) -> bool:
    """Validate a URL for safety.

    Checks:
    1. Scheme is allowed.
    2. Does not start with '-' (prevent argument injection).
    3. Basic URL structure.

    Args:
        url: URL string to validate.

    Returns:
        bool: True if valid, False otherwise.
    """
    if not url:
        return False

    # Prevent argument injection
    if url.startswith("-"):
        logger.warning("URL starts with '-': %s", url)
        return False

    try:
        parsed = urlparse(url)
        # Handle scp-like git syntax (user@host:path) which urlparse might not handle well as a scheme
        if not parsed.scheme:
            # Check for git@github.com:user/repo.git format
            if re.match(r"^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+:", url):
                return True
            logger.warning("URL has no scheme: %s", url)
            return False

        if parsed.scheme not in ALLOWED_SCHEMES:
            logger.warning("URL scheme not allowed: %s", parsed.scheme)
            return False

        return True

    except Exception as e:
        logger.warning("Failed to parse URL %s: %s", url, e)
        return False


def validate_safe_path(path: str | Path, base_dir: Path) -> bool:
    """Validate that a path resolves to a location inside the base directory.

    Prevents path traversal attacks.

    Args:
        path: Path to validate (string or Path object).
        base_dir: The trusted base directory.

    Returns:
        bool: True if safe, False otherwise.
    """
    try:
        base_dir = base_dir.resolve()
        target_path = (base_dir / path).resolve()
        
        # Check if target_path is relative to base_dir
        # is_relative_to is available in Python 3.9+
        try:
            target_path.relative_to(base_dir)
            return True
        except ValueError:
            logger.warning("Path traversal detected: %s is not inside %s", target_path, base_dir)
            return False

    except Exception as e:
        logger.warning("Failed to validate path %s: %s", path, e)
        return False
