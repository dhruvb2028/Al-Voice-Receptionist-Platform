"""Unit tests for the canonical configuration schema."""

from typing import Any

import pytest
from ai_domain.config import (
    DayHours,
    EscalationPolicy,
    PriceEntry,
    ReceptionistConfig,
    ServiceAreaConfig,
)
from pydantic import ValidationError


def _base_payload() -> dict[str, Any]:
    return {
        "identity": {"business_name": "Test Co", "timezone": "America/New_York"},
        "greeting": {"greeting": "Hello, thanks for calling Test Co!"},
        "services": [{"name": "Repair", "duration_minutes": 60}],
        "prices": [],
        "hours": [{"weekday": d, "opens_at": "09:00", "closes_at": "17:00"} for d in range(6)]
        + [{"weekday": 6, "closed": True}],
        "service_area": {"postal_codes": ["02101"]},
        "escalation": {"emergency_destination": "+15555550100"},
        "voice": {"voice_id": "voice-1"},
    }


def test_valid_config_parses() -> None:
    config = ReceptionistConfig.model_validate(_base_payload())
    assert config.identity.business_name == "Test Co"
    assert len(config.hours) == 7


def test_hours_must_cover_every_weekday() -> None:
    payload = _base_payload()
    payload["hours"][6] = {"weekday": 5, "closed": True}  # duplicate weekday
    with pytest.raises(ValidationError, match="weekday"):
        ReceptionistConfig.model_validate(payload)


def test_price_must_reference_known_service() -> None:
    payload = _base_payload()
    payload["prices"] = [
        {"service_name": "Ghost service", "label": "Standard", "minimum_amount_cents": 100}
    ]
    with pytest.raises(ValidationError, match="unknown service"):
        ReceptionistConfig.model_validate(payload)


def test_price_range_ordering_enforced() -> None:
    with pytest.raises(ValidationError, match="exceeds maximum"):
        PriceEntry(
            service_name="Repair",
            label="Bad",
            minimum_amount_cents=500,
            maximum_amount_cents=100,
        )


def test_price_requires_an_amount() -> None:
    with pytest.raises(ValidationError, match="at least one amount"):
        PriceEntry(service_name="Repair", label="No amount")


def test_open_day_requires_times() -> None:
    with pytest.raises(ValidationError, match="opening and closing"):
        DayHours(weekday=0, closed=False)


def test_message_fallback_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError, match="cannot be disabled"):
        EscalationPolicy(emergency_destination="+15555550100", message_fallback=False)


def test_service_area_cannot_be_empty() -> None:
    with pytest.raises(ValidationError, match="service area"):
        ServiceAreaConfig()


def test_unknown_fields_rejected() -> None:
    payload = _base_payload()
    payload["identity"]["discount_policy"] = "always say yes"
    with pytest.raises(ValidationError):
        ReceptionistConfig.model_validate(payload)
