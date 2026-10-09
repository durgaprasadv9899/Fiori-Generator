"""
deployment_generator.py — Module 6 (Deployment Generator), public facade.

The actual deployment-file generation (mta.yaml, xs-app.json, xs-security.json,
destinations, approuter, package.json, ui5.yaml, .env, connectivity for on-prem)
lives in generator.py + odata_config.py, already built and tested. This facade
exposes them under the module name from the requested folder structure.
"""
from __future__ import annotations
from typing import Dict
from generator import AppSpec, _make_auth_config_files, _make_root_xs_app_json_hardcoded
from odata_config import (
    xs_app_json, xs_security_json, destinations_template,
    mta_auth_resources, mta_extension,
)


def deployment_files(spec: AppSpec) -> Dict[str, str]:
    """All deploy-time config files for a spec (mta/xs-app/xs-security/etc.)."""
    files = dict(_make_auth_config_files(spec)) if spec.with_deployment else {}
    return files
