"""
screen_planner.py — Module 4 (Screen Planner), public entry point.

Screen planning is performed together with requirement analysis in
business_analyzer.analyze() (one AI call decides app type, actions, AND the UI
blocks / list vs detail fields). This module exposes the screen-planning view of
that result as a stable, separately-importable API, matching the requested
folder structure.
"""
from __future__ import annotations
from typing import Dict
from business_analyzer import analyze as _analyze, KNOWN_BLOCKS


def plan_screens(requirement: str, model: Dict, ai_fn) -> Dict:
    """Return just the screen-plan portion of the build plan."""
    plan = _analyze(requirement, model, ai_fn)
    return {
        "app_type": plan["app_type"],
        "blocks": plan["blocks"],
        "list_fields": plan["list_fields"],
        "detail_fields": plan["detail_fields"],
        "child_entity_set": plan["child_entity_set"],
    }


def block_catalog() -> list:
    """The UI building blocks the planner may select from."""
    return list(KNOWN_BLOCKS)
