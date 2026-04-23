from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.models.audit import AuditLog


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log_action(
        self,
        action: str,
        entity_type: str,
        entity_id: Any,
        user: Optional[str] = "system",
        details: Optional[Dict] = None,
    ) -> AuditLog:
        log_entry = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            user=user,
            details=details or {},
        )
        self.db.add(log_entry)
        await self.db.commit()
        await self.db.refresh(log_entry)
        return log_entry

    async def get_logs(
        self, entity_type: Optional[str] = None, limit: int = 50
    ) -> List[AuditLog]:
        query = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        if entity_type:
            query = query.where(AuditLog.entity_type == entity_type)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def prune_old_logs(self, retention_days: int) -> int:
        """Delete audit log entries older than retention_days.

        Returns the number of deleted rows.
        """
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await self.db.execute(
            delete(AuditLog).where(AuditLog.timestamp < cutoff)
        )
        await self.db.commit()
        return result.rowcount

    async def count_logs(self) -> int:
        """Return the total number of audit log entries."""
        result = await self.db.execute(select(func.count(AuditLog.id)))
        return result.scalar_one() or 0
