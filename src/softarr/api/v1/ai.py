"""AI assistant API endpoints.

Provides a single POST /ask endpoint that accepts a scenario and context,
delegates to the AIService, and returns the assistant's response.

Allowed scenarios: discovery, risk, comparison.
AI must be enabled in settings and the user must be authenticated.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from softarr.auth.dependencies import require_auth
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.middleware.rate_limit import limiter
from softarr.services.ai_service import VALID_SCENARIOS, AIService

logger = logging.getLogger("softarr.api.ai")

router = APIRouter()


class AIAskRequest(BaseModel):
    """Request body for the AI ask endpoint."""

    scenario: str
    context: str
    user_hint: Optional[str] = None  # Optional extra context (software name, version)

    @field_validator("scenario")
    @classmethod
    def validate_scenario(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in VALID_SCENARIOS:
            raise ValueError(
                f"scenario must be one of: {', '.join(sorted(VALID_SCENARIOS))}"
            )
        return v

    @field_validator("context")
    @classmethod
    def validate_context(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("context must not be empty")
        if len(v) > 2000:
            raise ValueError("context exceeds maximum length of 2000 characters")
        return v


@router.post("/ask")
@limiter.limit("10/minute")
async def ai_ask(
    request: Request,
    body: AIAskRequest,
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_auth),
):
    """Submit an AI-assisted query.

    Accepts a scenario (discovery, risk, or comparison) and a context string.
    Returns the assistant's structured text response.

    Rate limited to 10 requests per minute per IP in addition to the
    hourly process-wide token bucket configured in settings.
    """
    service = AIService(ini)

    # Combine context with optional user hint
    context = body.context
    if body.user_hint:
        context = f"{body.user_hint}: {context}"

    try:
        response_text = await service.ask(
            scenario=body.scenario,
            context=context,
            user=user.get("u"),
        )
    except RuntimeError as exc:
        if "not enabled" in str(exc).lower():
            raise HTTPException(status_code=503, detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "scenario": body.scenario,
        "response": response_text,
    }
