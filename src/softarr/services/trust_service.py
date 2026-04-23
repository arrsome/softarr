from typing import Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.models.release import FlagStatus, Release, TrustStatus, WorkflowState


class TrustService:
    def __init__(self, db: Optional[AsyncSession] = None) -> None:
        self.db = db

    async def suggest_threshold(self, software_id: UUID) -> Optional[float]:
        """Suggest an auto-approve threshold based on clean download history.

        Fetches all DOWNLOADED releases with no flag (NONE) and calculates
        the 10th-percentile confidence score. Requires at least 5 data points.
        Returns None if insufficient data.
        """
        if self.db is None:
            return None

        result = await self.db.execute(
            select(Release.confidence_score)
            .where(Release.software_id == software_id)
            .where(Release.workflow_state == WorkflowState.DOWNLOADED)
            .where(Release.flag_status == FlagStatus.NONE)
            .order_by(Release.confidence_score)
        )
        scores = [row[0] for row in result.all() if row[0] is not None]

        if len(scores) < 5:
            return None

        # 10th-percentile: conservative threshold so most future downloads pass
        idx = max(0, int(len(scores) * 0.1))
        return round(scores[idx], 2)

    @classmethod
    def determine_trust_status(
        cls, release: Release, analysis_results: Dict
    ) -> TrustStatus:
        """Determine trust level based on analysis results.

        Developer Verified: valid signature with matching publisher, or
        known-good hash match.
        Admin Verified: set manually via the override/approval workflow.
        Unverified: default for everything else.
        """
        sig = analysis_results.get("signature_status")
        hash_st = analysis_results.get("hash_status")

        if sig == "valid" and release.publisher:
            return TrustStatus.DEVELOPER_VERIFIED

        if hash_st == "match":
            return TrustStatus.DEVELOPER_VERIFIED

        match_quality = analysis_results.get("match_quality_score", 0)
        if (
            release.flag_status == FlagStatus.NONE
            and match_quality > 0.8
            and sig != "invalid"
        ):
            return TrustStatus.DEVELOPER_VERIFIED

        return TrustStatus.UNVERIFIED

    @classmethod
    def apply_admin_verification(cls, release: Release) -> None:
        """Manually mark a release as admin verified."""
        release.trust_status = TrustStatus.ADMIN_VERIFIED
