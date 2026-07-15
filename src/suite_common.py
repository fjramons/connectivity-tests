#!/usr/bin/env python3
"""Shared suite/config resolution for the Dev-PC-tier generators
(generate_test_spec.py, generate_server_manifests.py,
generate_standalone_script.py, generate_report.py).

Only uses the standard library, consistent with the generators that don't
otherwise need PyYAML.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERIC_CONFIG = ROOT / "connectivity-tests.toml"
CONFIG_TEMPLATE = ROOT / "connectivity-tests.toml.template"
SUITE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def resolve_suite(suite: str | None) -> str:
    """Resolves --suite (or the TEST_SUITE env var) into a validated suite name,
    falling back to "default" if neither is given."""
    suite = suite or os.environ.get("TEST_SUITE")
    if not suite:
        suite = "default"
        print(f"ℹ️  No --suite/TEST_SUITE given: using suite '{suite}'.")
    if not SUITE_NAME_RE.match(suite):
        raise SystemExit(
            f"Invalid --suite {suite!r}: must be a plain name "
            "(letters/digits/./-/_ only, no leading '.', no '/')."
        )
    return suite


def check_suite_exists(suite: str) -> None:
    suite_dir = ROOT / "inputs" / suite
    if not suite_dir.is_dir():
        existing = sorted(
            p.name for p in (ROOT / "inputs").iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
        raise SystemExit(
            f"❌ Suite '{suite}' not found: {suite_dir} does not exist.\n"
            f"   Existing suites: {', '.join(existing) or '(none yet)'}\n"
            f"   Create {suite_dir}/ with your CSVs, or pick an existing "
            "suite with --suite <name> / export TEST_SUITE=<name>."
        )


def resolve_config_path(explicit: Path | None, suite: str) -> Path:
    """inputs/<suite>/connectivity-tests.toml if it exists, else the generic
    connectivity-tests.toml (auto-created from connectivity-tests.toml.template
    on first use if it doesn't exist yet)."""
    if explicit:
        return explicit
    suite_config = ROOT / "inputs" / suite / "connectivity-tests.toml"
    if suite_config.exists():
        return suite_config
    if not GENERIC_CONFIG.exists() and CONFIG_TEMPLATE.exists():
        GENERIC_CONFIG.write_text(CONFIG_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"ℹ️  Created {GENERIC_CONFIG} from {CONFIG_TEMPLATE} (no {suite_config} found)")
    return GENERIC_CONFIG
