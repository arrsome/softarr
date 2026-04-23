"""Core analysis engine.

The ``sensitivity`` parameter controls how aggressively releases are flagged:

  low    -- requires 3+ heuristic flags for RESTRICTED; 2+ for WARNING
  medium -- requires 2+ heuristic flags for RESTRICTED; 1+ for WARNING (default)
  high   -- 1 heuristic flag -> RESTRICTED; any suspicious signal -> WARNING

Hash mismatches and invalid signatures always produce BLOCKED regardless of
sensitivity, as they represent unambiguous integrity failures.

When ``antipiracy_enabled`` is True the release name and asset filenames are
also checked against a list of known piracy-related keywords. Any match results
in BLOCKED with a clear reason message, regardless of sensitivity level.
"""

from typing import Any, Dict

from softarr.analysis.antipiracy import check_release_for_piracy
from softarr.analysis.archive import scan_asset_names
from softarr.analysis.hash import check_hash
from softarr.analysis.signature import check_signature
from softarr.analysis.suspicious import detect_suspicious_in_list
from softarr.models.release import FlagStatus

# Sensitivity level -> (restricted_threshold, warning_threshold)
# restricted_threshold: how many heuristic flags are needed for RESTRICTED
# warning_threshold:    how many heuristic flags are needed for WARNING
_SENSITIVITY_THRESHOLDS = {
    "low": (3, 2),
    "medium": (2, 1),
    "high": (1, 0),  # 0 = any flag triggers WARNING
}
_DEFAULT_SENSITIVITY = "medium"


class AnalysisEngine:
    """Core analysis engine that enriches release data with security metadata."""

    @staticmethod
    def analyze(
        release_data: Dict,
        sensitivity: str = _DEFAULT_SENSITIVITY,
        antipiracy_enabled: bool = False,
    ) -> Dict[str, Any]:
        """Analyse a release and return enriched metadata.

        Parameters
        ----------
        release_data:
            Dict produced by an adapter (or release service).
        sensitivity:
            Heuristic aggressiveness -- "low", "medium", or "high".
            Falls back to "medium" for unrecognised values.
        antipiracy_enabled:
            When True, check release name and asset filenames against the
            anti-piracy keyword list. Any match immediately produces BLOCKED.
        """
        signature_status = check_signature(release_data)
        hash_status = check_hash(release_data)

        # Extract asset names from raw GitHub release data or similar
        raw = release_data.get("raw_data", {})
        asset_names = [
            a.get("name", "") for a in raw.get("assets", []) if a.get("name")
        ]

        unusual_files = scan_asset_names(asset_names)
        suspicious_patterns = detect_suspicious_in_list(asset_names)

        # Score source trust based on source type
        source_type = release_data.get("source_type", "")
        source_trust_score = _calculate_source_trust(source_type, raw)

        # Score match quality based on publisher and metadata
        match_quality_score = _calculate_match_quality(release_data)

        # Anti-piracy scan (runs before other flag logic)
        piracy_hits: list[str] = []
        if antipiracy_enabled:
            release_name = release_data.get("name", "") or release_data.get(
                "version", ""
            )
            piracy_hits = check_release_for_piracy(release_name, asset_names)

        # Determine flags
        flags = []
        if piracy_hits:
            flags.append(
                f"Anti-piracy filter: release contains prohibited keywords: {', '.join(piracy_hits)}"
            )
        if suspicious_patterns:
            flags.append(
                f"Suspicious patterns detected: {', '.join(suspicious_patterns)}"
            )
        if unusual_files:
            flags.append(f"Unusual files found: {', '.join(unusual_files)}")
        if signature_status == "invalid":
            flags.append("Digital signature is invalid")
        if hash_status == "mismatch":
            flags.append("Hash mismatch against known-good value")

        # Determine flag severity using sensitivity-aware thresholds.
        # Hash/signature failures are always BLOCKED regardless of sensitivity.
        norm_sensitivity = sensitivity.lower() if sensitivity else _DEFAULT_SENSITIVITY
        restricted_thresh, warning_thresh = _SENSITIVITY_THRESHOLDS.get(
            norm_sensitivity, _SENSITIVITY_THRESHOLDS[_DEFAULT_SENSITIVITY]
        )

        heuristic_flags = [
            f
            for f in flags
            if "Hash mismatch" not in f and "Digital signature" not in f
        ]
        h_count = len(heuristic_flags)

        if piracy_hits or hash_status == "mismatch" or signature_status == "invalid":
            flag_status = FlagStatus.BLOCKED
        elif h_count >= restricted_thresh:
            flag_status = FlagStatus.RESTRICTED
        elif h_count > warning_thresh:
            flag_status = FlagStatus.WARNING
        elif h_count > 0 and warning_thresh == 0:
            # high sensitivity: any flag -> WARNING
            flag_status = FlagStatus.WARNING
        else:
            flag_status = FlagStatus.NONE

        confidence = (source_trust_score + match_quality_score) / 2

        return {
            "signature_status": signature_status,
            "hash_status": hash_status,
            "unusual_file_detection": unusual_files,
            "suspicious_naming": suspicious_patterns,
            "source_trust_score": source_trust_score,
            "match_quality_score": match_quality_score,
            "flag_status": flag_status,
            "flag_reasons": flags,
            "confidence_score": round(confidence, 3),
            "sensitivity": norm_sensitivity,
        }


def _calculate_source_trust(source_type: str, raw_data: Dict) -> float:
    """Calculate trust score based on the release source."""
    base_scores = {
        "github": 0.80,
        "usenet": 0.30,
    }
    score = base_scores.get(source_type, 0.50)

    # Boost for verified GitHub authors
    if source_type == "github":
        author = raw_data.get("author", {})
        if author.get("site_admin"):
            score = min(score + 0.10, 1.0)

    return round(score, 3)


def _calculate_match_quality(release_data: Dict) -> float:
    """Calculate how well the release matches the software definition."""
    score = 0.5

    if release_data.get("publisher") and release_data.get("expected_publisher"):
        if (
            release_data["publisher"].lower()
            == release_data["expected_publisher"].lower()
        ):
            score += 0.3

    if release_data.get("version"):
        score += 0.1

    if release_data.get("source_origin"):
        score += 0.1

    return min(round(score, 3), 1.0)
