"""The migration 0006 seed rows as ``LoadedPolicy`` values, for tests that need no database.

Read from the migration file itself, so a test can never drift from the seeded numbers.
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
from typing import Any

from agent_lab.agent.policy.rules import LoadedPolicy, parse_rule


def _load_seed() -> list[tuple[str, str, str, str, dict[str, Any]]]:
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0006_seed_policies.py"
    spec = importlib.util.spec_from_file_location("seed_0006_helper", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed: list[tuple[str, str, str, str, dict[str, Any]]] = module.SEED
    return seed


SEED = _load_seed()


def seed_policies(**overrides: dict[str, Any]) -> tuple[LoadedPolicy, ...]:
    """All thirteen rows, ids in seed order. ``overrides`` replaces a row's rule JSON by code."""
    return tuple(
        LoadedPolicy(
            id=i,
            code=code,
            tier=kind,  # type: ignore[arg-type]
            description=description,
            params=parse_rule(code, overrides.get(code, rule)),
            active=True,
        )
        for i, (code, kind, _severity, description, rule) in enumerate(SEED, start=1)
    )


def deactivate(policies: tuple[LoadedPolicy, ...], code: str) -> tuple[LoadedPolicy, ...]:
    return tuple(dataclasses.replace(p, active=False) if p.code == code else p for p in policies)
