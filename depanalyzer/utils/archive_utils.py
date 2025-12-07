"""Safe archive extraction utilities.

This module provides secure archive extraction functions that protect against
path traversal attacks (Zip Slip, Tar Slip) and symbolic link attacks.
"""

import logging
import sys
import tarfile
import zipfile
from pathlib import Path

logger = logging.getLogger("depanalyzer.utils.archive_utils")


class ArchiveSecurityError(Exception):
    """Raised when archive contains potentially malicious paths."""

    pass


def _is_path_safe(member_path: Path, target_dir: Path) -> bool:
    """Check if extracted path stays within target directory.

    Args:
        member_path: Relative path of the archive member.
        target_dir: Target extraction directory (must be resolved).

    Returns:
        bool: True if the path is safe, False otherwise.
    """
    try:
        resolved = (target_dir / member_path).resolve()
        return resolved.is_relative_to(target_dir)
    except (ValueError, RuntimeError):
        return False


def safe_extract_zip(archive_path: Path, target_dir: Path) -> None:
    """Safely extract ZIP archive with path traversal protection.

    Args:
        archive_path: Path to ZIP file.
        target_dir: Target extraction directory.

    Raises:
        ArchiveSecurityError: If archive contains path traversal attempts.
        zipfile.BadZipFile: If archive is corrupted.
    """
    target_dir = target_dir.resolve()

    with zipfile.ZipFile(archive_path, "r") as zip_ref:
        for member in zip_ref.namelist():
            member_path = Path(member)

            # Check for absolute paths or parent directory references
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ArchiveSecurityError(
                    f"Zip Slip detected: {member} contains path traversal"
                )

            # Verify resolved path stays within target
            if not _is_path_safe(member_path, target_dir):
                raise ArchiveSecurityError(
                    f"Zip Slip detected: {member} escapes target directory"
                )

        # All members validated, safe to extract
        zip_ref.extractall(target_dir)


def safe_extract_tar(archive_path: Path, target_dir: Path, mode: str = "r:*") -> None:
    """Safely extract TAR archive with path traversal protection.

    Args:
        archive_path: Path to TAR file.
        target_dir: Target extraction directory.
        mode: Tarfile open mode (r:gz, r:bz2, r:xz, etc.).

    Raises:
        ArchiveSecurityError: If archive contains path traversal attempts.
    """
    target_dir = target_dir.resolve()

    with tarfile.open(archive_path, mode) as tar_ref:
        # Python 3.12+ has built-in filter parameter
        if sys.version_info >= (3, 12):
            tar_ref.extractall(target_dir, filter="data")
        else:
            # Manual validation for older Python versions
            for member in tar_ref.getmembers():
                member_path = Path(member.name)

                # Check for absolute paths
                if member_path.is_absolute():
                    raise ArchiveSecurityError(
                        f"Tar Slip detected: {member.name} is absolute path"
                    )

                # Check for parent directory references
                if ".." in member_path.parts:
                    raise ArchiveSecurityError(
                        f"Tar Slip detected: {member.name} contains path traversal"
                    )

                # Check for symbolic link attacks
                if member.issym() or member.islnk():
                    link_target = Path(member.linkname)
                    if link_target.is_absolute() or ".." in link_target.parts:
                        raise ArchiveSecurityError(
                            f"Symlink attack detected: {member.name} -> {member.linkname}"
                        )

                # Verify resolved path stays within target
                if not _is_path_safe(member_path, target_dir):
                    raise ArchiveSecurityError(
                        f"Tar Slip detected: {member.name} escapes target directory"
                    )

            tar_ref.extractall(target_dir)


def safe_extract(archive_path: Path, target_dir: Path) -> bool:
    """Safely extract archive with automatic format detection.

    Args:
        archive_path: Path to archive file.
        target_dir: Target extraction directory.

    Returns:
        bool: True if extraction succeeded.

    Raises:
        ArchiveSecurityError: If archive contains malicious paths.
        ValueError: If archive format is not supported.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = archive_path.suffix.lower()
    name = archive_path.name.lower()

    if suffix == ".zip":
        safe_extract_zip(archive_path, target_dir)
    elif name.endswith((".tar.gz", ".tgz")):
        safe_extract_tar(archive_path, target_dir, "r:gz")
    elif name.endswith(".tar.bz2"):
        safe_extract_tar(archive_path, target_dir, "r:bz2")
    elif name.endswith(".tar.xz"):
        safe_extract_tar(archive_path, target_dir, "r:xz")
    elif suffix == ".tar":
        safe_extract_tar(archive_path, target_dir, "r:")
    else:
        raise ValueError(f"Unsupported archive format: {archive_path}")

    logger.info("Safely extracted %s to %s", archive_path, target_dir)
    return True


__all__ = [
    "ArchiveSecurityError",
    "safe_extract",
    "safe_extract_zip",
    "safe_extract_tar",
]
