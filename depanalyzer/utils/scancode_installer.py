"""Utilities to detect and install the ScanCode Toolkit locally."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

DEFAULT_SCANCODE_VERSION = os.environ.get("SCANCODE_VERSION", "32.4.1")
DEFAULT_INSTALL_ROOT = Path.home() / ".depanalyzer" / "scancode-toolkit"
_LOG = logging.getLogger("depanalyzer.utils.scancode_installer")


def is_scancode_executable(candidate: Path | str | None) -> bool:
    """Check whether a given path is a runnable ScanCode executable.

    Args:
        candidate: Path to test.

    Returns:
        True when the path exists and is executable, False otherwise.
    """
    if not candidate:
        return False
    try:
        path = Path(candidate)
    except (TypeError, ValueError):
        return False
    if not path.is_file():
        return False
    if os.access(path, os.X_OK):
        return True
    return path.suffix.lower() in {".bat", ".cmd", ".exe"}


def get_installed_scancode_path(install_root: Path = DEFAULT_INSTALL_ROOT) -> Path:
    """Return the expected ScanCode executable path under the install root.

    Args:
        install_root: Base directory where the toolkit is installed.

    Returns:
        Path pointing to the ScanCode executable (may not exist yet).
    """
    target = "scancode.bat" if sys.platform.startswith("win") else "scancode"
    return install_root / target


def _detect_python_tag() -> str:
    """Return the best-fit python tag string (py3.X) supported by bundles.

    Returns:
        Normalized python tag such as py3.9 or py3.12 (defaults to py3.9 when
        outside the supported range).
    """
    supported_minors = {9, 10, 11, 12, 13}
    minor = sys.version_info.minor if sys.version_info.major == 3 else 9
    if minor not in supported_minors:
        minor = 9
    return f"py3.{minor}"


def _detect_platform_tag() -> str:
    """Return platform tag used by ScanCode release assets.

    Returns:
        Platform tag string (windows/linux/macos).

    Raises:
        RuntimeError: When the current platform is unsupported.
    """
    plat = sys.platform
    if plat.startswith("win"):
        return "windows"
    if plat.startswith("linux"):
        return "linux"
    if plat == "darwin":
        return "macos"
    raise RuntimeError(f"Unsupported platform for ScanCode installer: {plat}")


def _build_download_url(version: str, py_tag: str, platform_tag: str) -> str:
    """Compose a download URL for the given version/platform/python combo.

    Args:
        version: ScanCode Toolkit version string (e.g., 32.4.1).
        py_tag: Python compatibility tag (e.g., py3.11).
        platform_tag: Platform tag (windows/linux/macos).

    Returns:
        Fully qualified download URL.
    """
    base = os.environ.get(
        "SCANCODE_BASE_URL",
        "https://github.com/aboutcode-org/scancode-toolkit/releases/download",
    )
    suffix = "zip" if platform_tag == "windows" else "tar.gz"
    filename = f"scancode-toolkit-v{version}_{py_tag}-{platform_tag}.{suffix}"
    return f"{base}/v{version}/{filename}"


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract an archive (.tar.* or .zip) into dest_dir.

    Uses safe extraction functions to prevent path traversal attacks (Zip Slip).

    Args:
        archive_path: Local path to downloaded archive.
        dest_dir: Destination directory for extraction.

    Returns:
        None

    Raises:
        ArchiveSecurityError: If archive contains path traversal attempts.
        RuntimeError: If archive format is unsupported.
    """
    from depanalyzer.utils.archive_utils import safe_extract_tar, safe_extract_zip

    if tarfile.is_tarfile(archive_path):
        safe_extract_tar(archive_path, dest_dir)
        return

    if zipfile.is_zipfile(archive_path):
        safe_extract_zip(archive_path, dest_dir)
        return

    raise RuntimeError(f"Unsupported archive format for ScanCode: {archive_path}")


def _find_extracted_root(extract_dir: Path) -> Path:
    """Identify the most likely root directory containing the ScanCode CLI.

    Args:
        extract_dir: Directory containing extracted toolkit contents.

    Returns:
        Path pointing to the directory that contains the scancode executable.

    Raises:
        RuntimeError: When extraction contents cannot be inspected.
    """
    try:
        children = [p for p in extract_dir.iterdir() if p.is_dir()]
    except OSError as exc:
        raise RuntimeError(f"Failed to inspect extracted content: {exc}") from exc

    if len(children) == 1:
        return children[0]
    if children:
        return extract_dir
    return extract_dir


def install_scancode(
    install_root: Path = DEFAULT_INSTALL_ROOT,
    version: Optional[str] = None,
    download_url: Optional[str] = None,
) -> Path:
    """Download and install the ScanCode Toolkit into a fixed directory.

    The download URL can be overridden via SCANCODE_DOWNLOAD_URL env var
    or the download_url argument to support different platforms or versions.

    Args:
        install_root: Destination directory for the extracted toolkit.
        version: Optional version string (e.g., 32.4.1). Defaults to 32.4.1 or
            SCANCODE_VERSION environment variable.
        download_url: Optional URL for the toolkit archive.

    Returns:
        Path to the installed ScanCode executable.

    Raises:
        RuntimeError: If download, extraction, or installation fails.
    """
    target_executable = get_installed_scancode_path(install_root)
    if is_scancode_executable(target_executable):
        _LOG.info("ScanCode already installed at %s", target_executable)
        return target_executable

    chosen_version = (
        version
        or os.environ.get("SCANCODE_VERSION")
        or DEFAULT_SCANCODE_VERSION
    )
    py_tag = _detect_python_tag()
    platform_tag = _detect_platform_tag()
    url = download_url or os.environ.get("SCANCODE_DOWNLOAD_URL") or _build_download_url(
        chosen_version, py_tag, platform_tag
    )
    _LOG.info(
        "Downloading ScanCode %s for %s/%s from %s",
        chosen_version,
        platform_tag,
        py_tag,
        url,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="scancode_dl_"))
    archive_name = Path(url.split("?")[0]).name or "scancode-toolkit.tar.gz"
    archive_path = tmp_dir / archive_name

    try:
        with urllib.request.urlopen(url) as resp, archive_path.open("wb") as out:
            shutil.copyfileobj(resp, out)

        extract_dir = Path(tempfile.mkdtemp(prefix="scancode_extract_"))
        try:
            _extract_archive(archive_path, extract_dir)
            content_root = _find_extracted_root(extract_dir)
            if install_root.exists():
                shutil.rmtree(install_root, ignore_errors=True)
            install_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(content_root), str(install_root))
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

        scancode_path = get_installed_scancode_path(install_root)
        if not is_scancode_executable(scancode_path):
            fallback = next(
                (
                    cand
                    for cand in install_root.rglob("scancode*")
                    if is_scancode_executable(cand)
                ),
                None,
            )
            if fallback and fallback.parent != install_root:
                install_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fallback, scancode_path)
            elif fallback:
                scancode_path = fallback

        if not is_scancode_executable(scancode_path):
            raise RuntimeError(
                f"ScanCode executable not found after extraction under {install_root}"
            )

        _LOG.info("ScanCode installed at %s", scancode_path)
        return scancode_path
    except Exception as exc:
        raise RuntimeError(f"Failed to install ScanCode: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
