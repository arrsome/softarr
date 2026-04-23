import hashlib
from typing import Dict, Optional


def check_hash(release_data: Dict) -> str:
    """Verify release hash against known-good values.

    In production, compare downloaded file hashes against hashes published
    by the vendor or stored in the software definition. This implementation
    checks for hash metadata presence in the release data.
    """
    known_hashes = release_data.get("known_hashes", {})
    if not known_hashes:
        return "unknown"

    actual_hash = release_data.get("computed_hash")
    if not actual_hash:
        return "unknown"

    for algo, expected in known_hashes.items():
        if actual_hash.lower() == expected.lower():
            return "match"

    return "mismatch"


def compute_file_hash(file_path: str, algorithm: str = "sha256") -> Optional[str]:
    """Compute hash of a local file."""
    try:
        h = hashlib.new(algorithm)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError, ValueError:
        return None
