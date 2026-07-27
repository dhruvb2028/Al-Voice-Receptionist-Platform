"""Prompt assembly for live turns.

Builds the system prompt from the versioned prompt files
(``services/voice/prompts/*.md``), the tenant's approved configuration,
and a structured state summary from the deterministic state machine.

Context strategy: the model never receives the unbounded transcript —
the engine sends recent turns plus this structured summary (state,
confirmed facts, unresolved fields), and the prompt carries only
tenant-approved facts.
"""

import re
from pathlib import Path

from ai_domain.config import ReceptionistConfig
from ai_domain.state_machine import ConversationStateMachine

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_METADATA_RE = re.compile(r"<!--\s*(.*?)\s*-->", re.S)


def load_prompt(name: str) -> tuple[str, dict[str, str]]:
    """Load a prompt file and its version metadata (from the leading
    HTML comment block)."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    match = _METADATA_RE.match(text.strip())
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if value:
                metadata[key.strip()] = value.strip()
        text = text[match.end() :].strip()
    return text, metadata


def build_system_prompt(config: ReceptionistConfig) -> tuple[str, dict[str, str]]:
    """Base + vertical prompt, filled with tenant-approved context.

    Returns (prompt, version metadata) so prompt versions land in call
    telemetry.
    """
    base, base_meta = load_prompt("base")
    vertical, vertical_meta = load_prompt("home-services")

    persona = config.voice.speaking_style or "friendly, efficient, plain-spoken"
    filled = base.format(
        assistant_label="the automated assistant",
        business_name=config.identity.business_name,
        persona=persona,
    )
    vertical_filled = vertical.format(vertical="home services")

    versions = {
        "base_version": base_meta.get("version", "unknown"),
        "vertical_version": vertical_meta.get("version", "unknown"),
    }
    return f"{filled}\n\n{vertical_filled}", versions


def build_state_summary(machine: ConversationStateMachine) -> str:
    """Structured, compact state summary injected each turn.

    Facts here are authoritative (from tools and confirmations) — the
    model is told to trust them over its own memory of the transcript.
    """
    data = machine.data
    lines = [f"Call state: {machine.state.value}."]

    if data.confirmed_facts:
        confirmed = ", ".join(f"{k}={v}" for k, v in sorted(data.confirmed_facts.items()))
        lines.append(f"Confirmed facts (trust these): {confirmed}.")
    unresolved = data.unresolved_fields()
    if unresolved:
        lines.append(f"Still needed before booking: {', '.join(unresolved)}.")
    if data.service_area_ok is not None:
        lines.append(
            "Service area check: "
            + (
                "address is IN the service area."
                if data.service_area_ok
                else "address is OUTSIDE the service area — offer a message, not a booking."
            )
        )
    if data.presented_slots:
        lines.append(f"Slots already offered: {', '.join(data.presented_slots)}.")
    if data.selected_slot:
        lines.append(f"Caller selected: {data.selected_slot}.")
    if data.booking_status:
        lines.append(f"Booking status (authoritative): {data.booking_status}.")
    if data.urgency:
        lines.append(f"Assessed urgency: {data.urgency}.")
    return "\n".join(lines)
