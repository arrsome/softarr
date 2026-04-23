"""Unit tests for AIService."""

import pytest

from softarr.services.ai_service import AIService


class FakeIni:
    """Minimal IniSettingsManager stub for testing."""

    def __init__(self, settings: dict):
        self._settings = settings

    def get(self, key: str):
        return self._settings.get(key)


class TestAIServiceEnabled:
    def test_is_enabled_true(self):
        ini = FakeIni({"ai_enabled": "true"})
        svc = AIService(ini)
        assert svc.is_enabled() is True

    def test_is_enabled_false(self):
        ini = FakeIni({"ai_enabled": "false"})
        svc = AIService(ini)
        assert svc.is_enabled() is False

    def test_is_enabled_missing_defaults_false(self):
        ini = FakeIni({})
        svc = AIService(ini)
        assert svc.is_enabled() is False


class TestAIServiceAskValidation:
    @pytest.mark.asyncio
    async def test_disabled_raises_runtime_error(self):
        ini = FakeIni({"ai_enabled": "false"})
        svc = AIService(ini)
        with pytest.raises(RuntimeError, match="not enabled"):
            await svc.ask("discovery", "VLC")

    @pytest.mark.asyncio
    async def test_invalid_scenario_raises_value_error(self):
        ini = FakeIni({"ai_enabled": "true", "ai_rate_limit_per_hour": "1000"})
        svc = AIService(ini)
        with pytest.raises(ValueError, match="Invalid scenario"):
            await svc.ask("hacking", "something")

    @pytest.mark.asyncio
    async def test_empty_context_raises_value_error(self):
        ini = FakeIni({"ai_enabled": "true", "ai_rate_limit_per_hour": "1000"})
        svc = AIService(ini)
        with pytest.raises(ValueError, match="empty"):
            await svc.ask("discovery", "")

    @pytest.mark.asyncio
    async def test_context_too_long_raises_value_error(self):
        ini = FakeIni({"ai_enabled": "true", "ai_rate_limit_per_hour": "1000"})
        svc = AIService(ini)
        with pytest.raises(ValueError, match="exceeds maximum"):
            await svc.ask("discovery", "x" * 2001)


class TestBuildPrompt:
    def test_discovery_prompt(self):
        p = AIService._build_prompt("discovery", "VLC")
        assert "alternatives" in p.lower()
        assert "VLC" in p

    def test_risk_prompt(self):
        p = AIService._build_prompt("risk", "some release")
        assert "safe" in p.lower() or "risk" in p.lower()
        assert "some release" in p

    def test_comparison_prompt(self):
        p = AIService._build_prompt("comparison", "VLC vs MPV")
        assert "compare" in p.lower() or "choose" in p.lower()
        assert "VLC vs MPV" in p
