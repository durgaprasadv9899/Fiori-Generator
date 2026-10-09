"""
prompt_builder.py — builds the metadata-enriched AI prompt.

Instead of sending the AI only "Create Sales Order App", we enrich the prompt
with the parsed metadata summary so the model generates code against the REAL
entities/fields. Used by the analyzer; exposed here as a reusable builder.
"""
from __future__ import annotations
from typing import Dict
from metadata_parser import summarize_for_prompt


def build_requirement_prompt(requirement: str, model: Dict) -> str:
    """Business requirement + compact metadata context."""
    return (
        f"BUSINESS REQUIREMENT:\n{requirement.strip()}\n\n"
        f"BACKEND METADATA (use ONLY these entity sets and fields):\n"
        f"{summarize_for_prompt(model)}\n"
    )


def build_generation_context(requirement: str, model: Dict, plan: Dict) -> str:
    """Full context string (requirement + metadata + chosen plan) for the
    file-generation prompts, so generated views/controllers match the plan."""
    fields = ", ".join(plan.get("list_fields", []))
    return (
        build_requirement_prompt(requirement, model)
        + f"\nCHOSEN PLAN: app_type={plan.get('app_type')}, "
          f"entity_set={plan.get('primary_entity_set')}, "
          f"key={plan.get('key_field')}, title={plan.get('title_field')}, "
          f"list_fields=[{fields}], actions={plan.get('actions')}.\n"
    )
