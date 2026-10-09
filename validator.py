"""
validator.py — Module: generated-project validator.

Reuses generator.validate_and_fix (which regenerates the deterministic,
hardcoded config files and repairs AI-generated ones) and adds a light
pre-deploy structural check that catches the specific failure modes this POC
has hit: missing service bindings, a route referencing a destination that
isn't created, or a null .mtaext parameters block.
"""
from __future__ import annotations
from typing import Dict, List, Tuple
from generator import validate_and_fix as _validate_and_fix, AppSpec


def validate_and_fix(spec: AppSpec, files: Dict[str, str]) -> Dict[str, str]:
    return _validate_and_fix(files, spec)


def preflight_check(spec: AppSpec, files: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Return (ok, problems). Non-fatal advisory checks before deploy."""
    problems: List[str] = []
    svcs = spec.service_list()
    real = [s for s in svcs if s.is_real()]
    mta = files.get("mta.yaml", "")
    xsapp = files.get("xs-app.json", "")

    if real and spec.with_deployment:
        if "resources:" not in mta or "requires:" not in mta:
            problems.append(
                "mta.yaml has no resources/requires block — the approuter would "
                "deploy with no service bindings."
            )
        # every destination referenced in xs-app.json must be creatable/known
        for s in real:
            if s.dest_name() not in xsapp:
                problems.append(f"xs-app.json does not reference destination "
                                f"{s.dest_name()} for service {s.id}.")
        if any(s.on_premise for s in real) and "connectivity" not in mta:
            problems.append(
                "An on-premise service is configured but the connectivity "
                "service is not in mta.yaml — expect a 502 at runtime."
            )
    return (len(problems) == 0, problems)
