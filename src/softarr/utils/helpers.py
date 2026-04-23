import re
from typing import Dict


def normalize_version(version: str) -> str:
    """Normalize version strings for comparison.
    Strips leading 'v' and non-numeric/dot characters.
    """
    version = version.strip().lstrip("vV")
    return re.sub(r"[^0-9.]", "", version)


def is_suspicious_filename(filename: str) -> bool:
    """Quick check for suspicious filenames."""
    suspicious = [
        "crack",
        "keygen",
        "patch",
        "activator",
        "loader",
        "repack",
        "warez",
        "nulled",
    ]
    lower = filename.lower()
    return any(term in lower for term in suspicious)


def calculate_overall_risk(analysis: Dict) -> str:
    """Calculate a simple risk label from analysis results."""
    flag = analysis.get("flag_status")
    if hasattr(flag, "value"):
        flag = flag.value
    if flag == "blocked":
        return "high"
    if flag == "restricted":
        return "medium"
    if flag == "warning":
        return "low"
    return "none"
