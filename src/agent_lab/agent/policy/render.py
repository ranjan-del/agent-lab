"""The rules in words, for the prompt and the tool descriptions. Pure: rows in, text out.

Every number the model is told about comes from a loaded row through here, so the prompt and
the gate can never disagree about where the focus block is. Retyping a number in a prompt is
how the two drift apart.
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_lab.agent.policy.rules import (
    DailyWindow,
    LoadedPolicy,
    Markers,
    MaxCount,
    MaxHours,
    Minutes,
    NoticeHours,
    Tier,
)

_TIER_HEADINGS: dict[Tier, str] = {
    "hard": "Hard rules. Never broken. A write that breaks one is refused and not made.",
    "middle": (
        "Middle rules. Held in a normal week. A write that breaks one is not made; it waits "
        "for my approval."
    ),
    "soft": (
        "Soft preferences. You may set one aside on your own, as long as you say that you did."
    ),
}


def params_text(policy: LoadedPolicy) -> str:
    """The row's numbers in words, or an empty string for a rule that has none."""
    p = policy.params
    if isinstance(p, DailyWindow):
        return p.describe()
    if isinstance(p, Markers):
        return "marked by: " + ", ".join(p.markers)
    if isinstance(p, MaxCount):
        return f"at most {p.max}"
    if isinstance(p, MaxHours):
        return f"at most {p.max_hours:g} hours"
    if isinstance(p, Minutes):
        return f"{p.minutes} minutes"
    if isinstance(p, NoticeHours):
        return f"{p.hours} hours"
    return ""


def rule_line(policy: LoadedPolicy) -> str:
    numbers = params_text(policy)
    tail = f" ({numbers})" if numbers else ""
    return f"- {policy.code}: {policy.description}{tail}"


def rules_text(policies: Iterable[LoadedPolicy]) -> str:
    """Every active rule, grouped by tier, one line each, in id order."""
    active = sorted((p for p in policies if p.active), key=lambda p: p.id)
    blocks: list[str] = []
    for tier, heading in _TIER_HEADINGS.items():
        lines = [rule_line(p) for p in active if p.tier == tier]
        if lines:
            blocks.append("\n".join([heading, *lines]))
    return "\n\n".join(blocks)
