"""Prometheus metrics endpoint.

Exposes a ``GET /metrics`` endpoint in Prometheus text exposition format.
No authentication is required (standard Prometheus convention -- restrict
access at the network/reverse-proxy level if needed).

Metrics exposed:
  softarr_releases_total{state="..."} -- release count per workflow state
  softarr_software_total{monitored="true|false"} -- software entry count
  softarr_audit_log_entries_total -- total audit log entries
  softarr_scheduler_enabled -- 1 if scheduler is enabled, 0 otherwise
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.models.audit import AuditLog
from softarr.models.release import Release, WorkflowState
from softarr.models.software import Software

logger = logging.getLogger("softarr.metrics")

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def prometheus_metrics(
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    """Return Prometheus text exposition format metrics."""
    lines: list[str] = []

    def gauge(name: str, value: float, labels: dict | None = None) -> str:
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            return f"{name}{{{label_str}}} {value}"
        return f"{name} {value}"

    # -- Release counts by workflow state --
    lines.append("# HELP softarr_releases_total Number of releases per workflow state")
    lines.append("# TYPE softarr_releases_total gauge")
    for state in WorkflowState:
        result = await db.execute(
            select(func.count(Release.id)).where(Release.workflow_state == state)
        )
        count = result.scalar_one() or 0
        lines.append(gauge("softarr_releases_total", count, {"state": state.value}))

    # -- Software counts --
    lines.append("# HELP softarr_software_total Number of software entries")
    lines.append("# TYPE softarr_software_total gauge")
    for monitored in (True, False):
        result = await db.execute(
            select(func.count(Software.id)).where(Software.monitored == monitored)
        )
        count = result.scalar_one() or 0
        lines.append(
            gauge(
                "softarr_software_total",
                count,
                {"monitored": "true" if monitored else "false"},
            )
        )

    # -- Audit log entry count --
    lines.append("# HELP softarr_audit_log_entries_total Total audit log entries")
    lines.append("# TYPE softarr_audit_log_entries_total gauge")
    result = await db.execute(select(func.count(AuditLog.id)))
    audit_count = result.scalar_one() or 0
    lines.append(gauge("softarr_audit_log_entries_total", audit_count))

    # -- Scheduler enabled flag --
    lines.append("# HELP softarr_scheduler_enabled 1 if the scheduler is enabled")
    lines.append("# TYPE softarr_scheduler_enabled gauge")
    scheduler_on = (
        1 if (ini.get("scheduler_enabled") or "false").lower() == "true" else 0
    )
    lines.append(gauge("softarr_scheduler_enabled", scheduler_on))

    return "\n".join(lines) + "\n"
