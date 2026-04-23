"""Archive content scanner with safety boundaries.

Scans ZIP and TAR archives for suspicious or unusual files. Applies:
  - Max archive size check before opening
  - Max file count inside archive
  - Path traversal detection (zip slip protection)
  - Nested archive detection (flagged, not recursed into)
  - No extraction to disk -- names are inspected in-memory only
"""

import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import List

from softarr.analysis.suspicious import detect_suspicious_patterns

UNUSUAL_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".vbs",
    ".ps1",
    ".scr",
    ".com",
    ".dll",
    ".sys",
    ".msi",
    ".pif",
    ".hta",
    ".wsf",
}

ARCHIVE_EXTENSIONS = {
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
}

# Safety limits
MAX_ARCHIVE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_FILES_IN_ARCHIVE = 10_000
MAX_FILENAME_LENGTH = 500


def scan_archive_contents(file_path: str) -> List[str]:
    """Scan archive contents for unusual or suspicious files.

    Returns a list of flagged filenames. Does NOT extract files to disk.
    """
    flagged = []
    path = Path(file_path)

    if not path.exists():
        return flagged

    # Size check before opening
    try:
        file_size = path.stat().st_size
    except OSError:
        return flagged

    if file_size > MAX_ARCHIVE_SIZE_BYTES:
        flagged.append(
            f"[SKIPPED] Archive exceeds {MAX_ARCHIVE_SIZE_BYTES // (1024 * 1024)} MB size limit"
        )
        return flagged

    try:
        if zipfile.is_zipfile(file_path):
            flagged.extend(_scan_zip(file_path))
        elif tarfile.is_tarfile(file_path):
            flagged.extend(_scan_tar(file_path))
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
        flagged.append(f"[ERROR] Failed to scan archive: {type(e).__name__}")

    return flagged


def _scan_zip(file_path: str) -> List[str]:
    flagged = []
    with zipfile.ZipFile(file_path, "r") as zf:
        members = zf.infolist()

        if len(members) > MAX_FILES_IN_ARCHIVE:
            flagged.append(
                f"[SKIPPED] Archive contains {len(members)} files "
                f"(limit: {MAX_FILES_IN_ARCHIVE})"
            )
            return flagged

        for info in members:
            name = info.filename
            flagged.extend(_check_member(name))

    return list(set(flagged))


def _scan_tar(file_path: str) -> List[str]:
    flagged = []
    count = 0
    with tarfile.open(file_path, "r:*") as tf:
        for member in tf:
            count += 1
            if count > MAX_FILES_IN_ARCHIVE:
                flagged.append(
                    f"[SKIPPED] Archive contains more than {MAX_FILES_IN_ARCHIVE} files"
                )
                break

            name = member.name
            flagged.extend(_check_member(name))

            # Tar-specific: check for absolute paths or device files
            if member.issym() or member.islnk():
                flagged.append(f"[SYMLINK] {name}")
            if member.isdev():
                flagged.append(f"[DEVICE] {name}")

    return list(set(flagged))


def _check_member(name: str) -> List[str]:
    """Check a single archive member name for issues."""
    flagged = []

    if not name or len(name) > MAX_FILENAME_LENGTH:
        flagged.append("[BAD_NAME] Filename too long or empty")
        return flagged

    # Path traversal detection (zip slip)
    normalized = PurePosixPath(name)
    try:
        normalized.relative_to(".")
    except ValueError:
        pass
    if ".." in name.split("/"):
        flagged.append(f"[PATH_TRAVERSAL] {name}")
        return flagged

    if name.startswith("/") or name.startswith("\\"):
        flagged.append(f"[ABSOLUTE_PATH] {name}")
        return flagged

    ext = Path(name).suffix.lower()

    # Flag unusual executables
    if ext in UNUSUAL_EXTENSIONS:
        flagged.append(name)

    # Flag nested archives (not recursed into)
    if ext in ARCHIVE_EXTENSIONS or name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        flagged.append(f"[NESTED_ARCHIVE] {name}")

    # Flag suspicious patterns
    if detect_suspicious_patterns(name):
        flagged.append(name)

    return flagged


def scan_asset_names(asset_names: List[str]) -> List[str]:
    """Scan a list of asset/file names without downloading archives.

    Used for pre-download analysis of GitHub release asset names, etc.
    """
    flagged = []
    for name in asset_names:
        if detect_suspicious_patterns(name):
            flagged.append(name)
        ext = Path(name).suffix.lower()
        if ext in UNUSUAL_EXTENSIONS:
            flagged.append(name)
    return list(set(flagged))
