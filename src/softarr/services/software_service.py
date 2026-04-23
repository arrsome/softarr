from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.models.software import Software
from softarr.schemas.software import SoftwareCreate, SoftwareResponse, SoftwareUpdate


class SoftwareService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_software(self, software_in: SoftwareCreate) -> SoftwareResponse:
        db_software = Software(
            canonical_name=software_in.canonical_name,
            aliases=software_in.aliases,
            expected_publisher=software_in.expected_publisher,
            supported_os=software_in.supported_os,
            architecture=software_in.architecture,
            version_format_rules=software_in.version_format_rules,
            source_preferences=software_in.source_preferences,
            notes=software_in.notes,
        )
        self.db.add(db_software)
        await self.db.commit()
        await self.db.refresh(db_software)
        return SoftwareResponse.model_validate(db_software)

    async def get_all_software(self) -> List[SoftwareResponse]:
        result = await self.db.execute(
            select(Software).order_by(Software.canonical_name)
        )
        softwares = result.scalars().all()
        return [SoftwareResponse.model_validate(s) for s in softwares]

    async def get_all_software_paginated(
        self, page: int = 1, page_size: int = 50
    ) -> Tuple[List[SoftwareResponse], int]:
        """Return a page of software entries and the total count.

        Args:
            page: 1-based page number.
            page_size: Number of entries per page.

        Returns:
            A tuple of (items, total_count).
        """
        offset = (page - 1) * page_size

        count_result = await self.db.execute(select(func.count()).select_from(Software))
        total = count_result.scalar_one()

        result = await self.db.execute(
            select(Software)
            .order_by(Software.canonical_name)
            .offset(offset)
            .limit(page_size)
        )
        softwares = result.scalars().all()
        items = [SoftwareResponse.model_validate(s) for s in softwares]
        return items, total

    async def get_software_by_id(self, software_id: UUID) -> Optional[Software]:
        result = await self.db.execute(
            select(Software).where(Software.id == software_id)
        )
        return result.scalar_one_or_none()

    async def update_software(
        self, software_id: UUID, update_in: SoftwareUpdate
    ) -> Optional[SoftwareResponse]:
        software = await self.get_software_by_id(software_id)
        if not software:
            return None
        update_data = update_in.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(software, key, value)
        await self.db.commit()
        await self.db.refresh(software)
        return SoftwareResponse.model_validate(software)

    async def delete_software(self, software_id: UUID) -> bool:
        software = await self.get_software_by_id(software_id)
        if not software:
            return False
        await self.db.delete(software)
        await self.db.commit()
        return True
