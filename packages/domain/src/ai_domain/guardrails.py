"""Output guardrails.

The last line of defense between generated text and the caller's ear.
Every generated reply passes through the pipeline AFTER the LLM and
BEFORE synthesis; guardrails run in domain code and cannot be disabled
by prompts, callers, or configuration. Each intervention produces a
guardrail event for persistence and review.

Order matters: the booking-confirmation gate runs before the price
firewall so a blocked confirmation cannot leak an invented price either.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from ai_domain.config import ReceptionistConfig
from ai_domain.conversation import GuardrailTrace, ToolExecutionTrace

logger = structlog.get_logger()

PRICE_DEFLECTION = "The team will confirm the exact price after reviewing the job."
CONFIRMATION_DEFLECTION = (
    "Let me double-check that on our end — the team will confirm your appointment shortly."
)
SERVICE_DEFLECTION = (
    "I'm not certain we offer that — I can take a message and have the team confirm for you."
)
AVAILABILITY_DEFLECTION = (
    "Let me have the team confirm exact times with you — can I take your "
    "number so they can follow up?"
)
SAFE_ERROR_RESPONSE = (
    "I'm sorry, I'm having a little trouble right now. Let me connect you "
    "with someone who can help."
)

# Currency amounts: $150, $1,299.50, 150 dollars, ninety-nine dollars is
# out of scope (spelled-out numbers are rare in TTS-bound text).
_CURRENCY_RE = re.compile(
    r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)|([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*"
    r"(?:dollars|bucks|USD)",
    re.IGNORECASE,
)

# Concessions the receptionist has no authority to grant. A discount is
# a price commitment even though no currency amount is spoken, so
# _CURRENCY_RE alone would let "20% off" through.
#
# Deliberately narrow: these match *granting* a concession, not refusing
# one. "I'm not able to offer a discount" is correct behaviour and must
# keep working, so a bare mention of "discount" is not enough to fire.
_CONCESSION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b[0-9]{1,2}\s*%\s*(?:off|discount)\b",
        r"\b[0-9]{1,2}\s*percent\s*(?:off|discount)\b",
        r"\bhalf[\s-]price\b",
        r"\b(?:for|at\s+no)\s+free\b|\bfree\s+of\s+charge\b|\bno\s+charge\b",
        # Allows a qualifier: "waive the callout fee".
        r"\bwaive\s+(?:the\s+)?(?:\w+\s+){0,2}(?:fee|charge|cost)s?\b",
        r"\bthrow\s+in\s+(?:a|an|the)\b",
        r"\bon\s+the\s+house\b",
    )
)

# Phrases implying a completed booking.
_CONFIRMATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\byou(?:'re| are)\s+(?:all\s+)?booked\b",
        r"\byour appointment (?:is|has been)\s+(?:confirmed|booked|scheduled|set)\b",
        r"\bI(?:'ve| have)?\s*scheduled\b",
        r"\bI(?:'ve| have)\s+(?:added|put)\s+you\s+(?:on|to)\s+the\s+calendar\b",
        r"\b(?:appointment|booking)\s+is\s+all\s+set\b",
        r"\bwe(?:'ve| have)\s+got\s+you\s+(?:down|booked)\b",
    )
)

# Time-slot mentions: "10 AM", "2:30 pm", "at 14:00".
_SLOT_RE = re.compile(r"\b([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm)\b", re.IGNORECASE)


def _normalize_amount_cents(raw: str) -> int:
    return int(round(float(raw.replace(",", "")) * 100))


def _amounts_in(text: str) -> list[int]:
    amounts = []
    for match in _CURRENCY_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        if raw:
            amounts.append(_normalize_amount_cents(raw))
    return amounts


@dataclass
class GuardrailContext:
    """What the pipeline knows about the current turn."""

    config: ReceptionistConfig
    tools: list[ToolExecutionTrace] = field(default_factory=list)
    #: True only when THIS turn contains a verified successful booking
    booking_confirmed_this_turn: bool = False


@dataclass
class GuardrailOutcome:
    text: str
    events: list[GuardrailTrace] = field(default_factory=list)
    blocked: bool = False


class GuardrailPipeline:
    """Runs every reply through the firewalls, in order."""

    def check(self, text: str, context: GuardrailContext) -> GuardrailOutcome:
        outcome = GuardrailOutcome(text=text)
        for stage in (
            self._booking_confirmation_gate,
            self._price_firewall,
            self._service_firewall,
            self._availability_firewall,
        ):
            stage(outcome, context)
        return outcome

    # -- booking confirmation gate ------------------------------------------

    def _booking_confirmation_gate(
        self, outcome: GuardrailOutcome, context: GuardrailContext
    ) -> None:
        if context.booking_confirmed_this_turn:
            return
        for pattern in _CONFIRMATION_PATTERNS:
            if pattern.search(outcome.text):
                outcome.events.append(
                    GuardrailTrace(
                        guardrail_type="booking_confirmation",
                        action="rewritten",
                        detail=f"blocked premature confirmation: {pattern.pattern}",
                    )
                )
                outcome.text = CONFIRMATION_DEFLECTION
                outcome.blocked = True
                logger.warning("guardrail_blocked_confirmation")
                return

    # -- price firewall -----------------------------------------------------

    def _approved_amounts(self, context: GuardrailContext) -> set[int]:
        approved: set[int] = set()
        for price in context.config.prices:
            if not price.approved or not price.customer_visible:
                continue
            if price.minimum_amount_cents is not None:
                approved.add(price.minimum_amount_cents)
            if price.maximum_amount_cents is not None:
                approved.add(price.maximum_amount_cents)
        # Amounts a tool returned this turn are approved by definition.
        for tool in context.tools:
            if tool.status == "success" and tool.result:
                approved.update(self._amounts_from_result(tool.result))
        return approved

    def _amounts_from_result(self, result: dict[str, Any]) -> set[int]:
        found: set[int] = set()
        for key, value in result.items():
            if isinstance(value, int) and key.endswith("_cents"):
                found.add(value)
            elif isinstance(value, dict):
                found.update(self._amounts_from_result(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        found.update(self._amounts_from_result(item))
        return found

    def _price_firewall(self, outcome: GuardrailOutcome, context: GuardrailContext) -> None:
        # A concession commits the business to a price without ever
        # naming one, so it is checked before the amount comparison.
        concession = next((p.pattern for p in _CONCESSION_PATTERNS if p.search(outcome.text)), None)
        if concession is not None:
            outcome.events.append(
                GuardrailTrace(
                    guardrail_type="price_invention",
                    action="rewritten",
                    detail="blocked an unauthorised concession",
                )
            )
            outcome.text = PRICE_DEFLECTION
            outcome.blocked = True
            logger.warning("guardrail_blocked_concession")
            return

        amounts = _amounts_in(outcome.text)
        if not amounts:
            return
        approved = self._approved_amounts(context)
        unapproved = [amount for amount in amounts if amount not in approved]
        if unapproved:
            outcome.events.append(
                GuardrailTrace(
                    guardrail_type="price_invention",
                    action="rewritten",
                    detail=f"blocked amounts (cents): {unapproved}",
                )
            )
            outcome.text = PRICE_DEFLECTION
            outcome.blocked = True
            logger.warning("guardrail_blocked_price", amounts=unapproved)

    # -- service firewall ---------------------------------------------------

    def _service_firewall(self, outcome: GuardrailOutcome, context: GuardrailContext) -> None:
        """Block affirmative claims of offering services the tenant does
        not have configured."""
        text_lower = outcome.text.lower()
        claim = re.search(
            r"\bwe\s+(?:do\s+)?(?:offer|provide|handle|do|install|repair|service)\s+"
            r"([a-z \-]{3,40})",
            text_lower,
        )
        if not claim:
            return
        claimed = claim.group(1).strip()
        configured = {s.name.lower() for s in context.config.services if s.active}
        # A claim is fine when any configured service name (or word of it)
        # appears in the claimed phrase.
        for name in configured:
            if name in claimed or any(word in claimed for word in name.split() if len(word) > 3):
                return
        outcome.events.append(
            GuardrailTrace(
                guardrail_type="service_invention",
                action="rewritten",
                detail=f"unconfigured service claim: '{claimed}'",
            )
        )
        outcome.text = SERVICE_DEFLECTION
        outcome.blocked = True
        logger.warning("guardrail_blocked_service_claim", claimed=claimed)

    # -- availability firewall ----------------------------------------------

    def _slots_from_tools(self, context: GuardrailContext) -> set[tuple[int, int]]:
        """(hour24, minute) pairs returned by the availability tool this turn."""
        offered: set[tuple[int, int]] = set()
        for tool in context.tools:
            if tool.tool_name != "check_availability" or not tool.result:
                continue
            for slot in tool.result.get("slots", []):
                try:
                    from datetime import datetime

                    start = datetime.fromisoformat(slot["start"])
                    offered.add((start.hour, start.minute))
                except (KeyError, ValueError, TypeError):
                    continue
        return offered

    def _availability_firewall(self, outcome: GuardrailOutcome, context: GuardrailContext) -> None:
        mentions = list(_SLOT_RE.finditer(outcome.text))
        if not mentions:
            return
        # Only enforce when the reply is offering appointment times.
        offering = re.search(
            r"\b(?:available|opening|slot|come out|appointment|schedule you|fit you in)\b",
            outcome.text,
            re.IGNORECASE,
        )
        if not offering:
            return
        offered = self._slots_from_tools(context)
        if not offered:
            # Times offered but no availability tool ran this turn.
            outcome.events.append(
                GuardrailTrace(
                    guardrail_type="availability_invention",
                    action="rewritten",
                    detail="times offered without a calendar result",
                )
            )
            outcome.text = AVAILABILITY_DEFLECTION
            outcome.blocked = True
            logger.warning("guardrail_blocked_availability")
            return
        for match in mentions:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            meridiem = match.group(3).lower()
            hour24 = (hour % 12) + (12 if meridiem == "pm" else 0)
            if (hour24, minute) not in offered:
                outcome.events.append(
                    GuardrailTrace(
                        guardrail_type="availability_invention",
                        action="rewritten",
                        detail=f"time {match.group(0)} not in calendar results",
                    )
                )
                outcome.text = AVAILABILITY_DEFLECTION
                outcome.blocked = True
                logger.warning("guardrail_blocked_uncalendared_time")
                return
