#!/usr/bin/env python3
"""Generates inputs/connectivity-test-spec.{yaml,json} from the CSVs in inputs/.

Runs on the dev PC with `uv run src/generate_test_spec.py`.
Requires PyYAML (declared in pyproject.toml, installed by `uv sync`).
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import itertools
import json
import os
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GENERIC_CONFIG = ROOT / "connectivity-tests.toml"
CONFIG_TEMPLATE = ROOT / "connectivity-tests.toml.template"

SERVERS_CSV_GLOB = "*Servers*.csv"
CLIENTS_CSV_GLOB = "*Clients*.csv"

UDP_RE = re.compile(r"\budp\b", re.IGNORECASE)
IP_RANGE_RE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s*-\s*(\d+\.\d+\.\d+\.\d+)$")
PORT_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
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


def clean(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.replace("\r", " ").split())


def split_multi(value: str | None) -> list[str]:
    """Split a field that may list several values separated by commas and/or newlines."""
    if not value:
        return []
    parts = re.split(r"[,\n]", value)
    return [clean(p) for p in parts if clean(p)]


def expand_ip_field(value: str | None) -> list[str]:
    """Expand an IP field into individual IPs: handles lists, and 'a.b.c.d-e.f.g.h' ranges."""
    ips: list[str] = []
    for token in split_multi(value):
        m = IP_RANGE_RE.match(token)
        if m:
            start, end = m.groups()
            start_int = int(ipaddress.IPv4Address(start))
            end_int = int(ipaddress.IPv4Address(end))
            for i in range(start_int, end_int + 1):
                ips.append(str(ipaddress.IPv4Address(i)))
        else:
            ips.append(token)
    return ips


def expand_port_field(value: str | None) -> list[int]:
    """Expand a port field into individual ports: handles lists and 'a-b' ranges."""
    ports: list[int] = []
    for token in split_multi(value):
        m = PORT_RANGE_RE.match(token)
        if m:
            start, end = (int(g) for g in m.groups())
            ports.extend(range(start, end + 1))
        else:
            ports.append(int(token))
    return ports


def classify_protocol(label: str) -> str:
    return "udp" if UDP_RE.search(label) else "tcp"


def pair_ports_protocols(
    ports: list[int], protocols: list[str], pairing_mode: str
) -> tuple[list[tuple[int, str]], str | None]:
    """Return list of (port, protocol_label) and an optional warning note."""
    if not protocols:
        return [(p, "") for p in ports], None
    if len(protocols) == 1:
        return [(p, protocols[0]) for p in ports], None
    if len(ports) == len(protocols):
        if pairing_mode == "one_to_one":
            return list(zip(ports, protocols)), None
        return list(itertools.product(ports, protocols)), None
    # Cardinalities differ and neither is 1 -- no unambiguous pairing possible.
    return (
        list(itertools.product(ports, protocols)),
        f"{len(ports)} ports and {len(protocols)} protocols don't match in count; "
        "the cross product was generated as a safe fallback.",
    )


def load_config(config_path: Path) -> dict:
    defaults = {"port_protocol_pairing": "one_to_one"}
    if config_path.exists():
        with config_path.open("rb") as f:
            defaults.update(tomllib.load(f))
    return defaults


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_test_cases_from_row(
    *,
    direction: str,
    source: dict,
    destination_location: str,
    destination_type: str,
    destination_description: str,
    destination_range_raw: str,
    ports_raw: str,
    protocol_raw: str,
    service: str,
    comments: str,
    origin_file: str,
    origin_row: int,
    pairing_mode: str,
    unresolved: list[dict],
    counter: itertools.count,
) -> list[dict]:
    dest_ips = expand_ip_field(destination_range_raw)
    dest_ports = expand_port_field(ports_raw)
    protocols = split_multi(protocol_raw)

    if not dest_ips or not dest_ports:
        unresolved.append(
            {
                "origin": {"file": origin_file, "row": origin_row},
                "reason": (
                    "Destination IP not found/parseable"
                    if not dest_ips
                    else "Port not found/parseable"
                ),
                "raw": {
                    "destination_range": destination_range_raw,
                    "port": ports_raw,
                    "protocol": protocol_raw,
                },
            }
        )
        return []

    pairs, warning = pair_ports_protocols(dest_ports, protocols, pairing_mode)
    if warning:
        unresolved.append(
            {
                "origin": {"file": origin_file, "row": origin_row},
                "reason": warning,
                "raw": {"port": ports_raw, "protocol": protocol_raw},
                "severity": "warning",
            }
        )

    automatable = direction == "local_cloud_domain_to_remote_cloud_domain"
    cases = []
    for ip, (port, protocol_label) in itertools.product(dest_ips, pairs):
        protocol_label = protocol_label or "unknown"
        case = {
            "id": f"{'c' if direction == 'local_cloud_domain_to_remote_cloud_domain' else 's'}{next(counter):04d}",
            "direction": direction,
            "automatable": automatable,
            "source": source,
            "destination": {
                "location": destination_location or None,
                "ip": ip,
                "type": destination_type or None,
                "description": destination_description or None,
            },
            "port": port,
            "protocol": classify_protocol(protocol_label),
            "protocol_label": protocol_label,
            "service": service or None,
            "comments": comments or None,
            "origin": {"file": origin_file, "row": origin_row},
        }
        if not automatable:
            case["note"] = (
                "Local cloud domain acts as server: this test requires manual execution "
                "from a client in Remote cloud domain. See README.md."
            )
        cases.append(case)
    return cases


def parse_servers_csv(path: Path, pairing_mode: str, unresolved: list[dict], counter: itertools.count) -> list[dict]:
    """'Servers' CSV: Local cloud domain is the server -> direction remote_cloud_domain_to_local_cloud_domain."""
    cases = []
    for i, row in enumerate(read_csv_rows(path), start=1):
        source = {
            "location": clean(row.get("Source Location")) or None,
            "type": None,
            "range": clean(row.get("Source Range")) or None,
            "description": clean(row.get("Source Description")) or None,
        }
        cases.extend(
            build_test_cases_from_row(
                direction="remote_cloud_domain_to_local_cloud_domain",
                source=source,
                destination_location=clean(row.get("Destination Location")),
                destination_type=clean(row.get("Destination Type")),
                destination_description=clean(row.get("Destination Description")),
                destination_range_raw=row.get("Destination Range", ""),
                ports_raw=row.get("Port Number / Range", ""),
                protocol_raw=row.get("Protocol", ""),
                service="",
                comments=clean(row.get("Comments")),
                origin_file=path.name,
                origin_row=i,
                pairing_mode=pairing_mode,
                unresolved=unresolved,
                counter=counter,
            )
        )
    return cases


def parse_clients_csv(path: Path, pairing_mode: str, unresolved: list[dict], counter: itertools.count) -> list[dict]:
    """'Clients' CSV: Local cloud domain is the client -> direction local_cloud_domain_to_remote_cloud_domain."""
    cases = []
    for i, row in enumerate(read_csv_rows(path), start=1):
        source = {
            "location": clean(row.get("Source Location")) or None,
            "type": clean(row.get("Source Type")) or None,
            "range": clean(row.get("Source Range")) or None,
            "description": clean(row.get("Source Description")) or None,
        }
        cases.extend(
            build_test_cases_from_row(
                direction="local_cloud_domain_to_remote_cloud_domain",
                source=source,
                destination_location=clean(row.get("Destination Location")),
                destination_type=None,
                destination_description=clean(row.get("Destination Description")),
                destination_range_raw=row.get("Destination Range", ""),
                ports_raw=row.get("Port Number / Range", ""),
                protocol_raw=row.get("Protocol", ""),
                service=clean(row.get("Service")),
                comments=clean(row.get("Comments")),
                origin_file=path.name,
                origin_row=i,
                pairing_mode=pairing_mode,
                unresolved=unresolved,
                counter=counter,
            )
        )
    return cases


def find_csv(inputs_dir: Path, pattern: str) -> Path:
    matches = sorted(inputs_dir.glob(pattern))
    if not matches:
        raise SystemExit(
            f"❌ No CSV matching {pattern!r} was found in {inputs_dir}.\n"
            "   If this suite hasn't been set up yet, add your CSVs there, "
            "or pick an existing suite with --suite <name> / "
            "export TEST_SUITE=<name>."
        )
    return matches[0]


def generate_from_csv(inputs_dir: Path, config: dict) -> dict:
    pairing_mode = config["port_protocol_pairing"]
    servers_csv = find_csv(inputs_dir, SERVERS_CSV_GLOB)
    clients_csv = find_csv(inputs_dir, CLIENTS_CSV_GLOB)

    unresolved: list[dict] = []
    counter = itertools.count(1)

    tests = []
    tests.extend(parse_clients_csv(clients_csv, pairing_mode, unresolved, counter))
    tests.extend(parse_servers_csv(servers_csv, pairing_mode, unresolved, counter))

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generator": "src/generate_test_spec.py",
            "source_files": [clients_csv.name, servers_csv.name],
            "port_protocol_pairing": pairing_mode,
        },
        "tests": tests,
        "unresolved": unresolved,
    }


YAML_HEADER = """\
# Connectivity test spec for Remote cloud domain <-> Local cloud domain.
# Generated by src/generate_test_spec.py -- editable by hand.
# After editing by hand, run `uv run src/generate_test_spec.py --from-yaml`
# to resync connectivity-test-spec.json WITHOUT re-reading the CSVs.
"""


def write_outputs(data: dict, yaml_path: Path, json_path: Path) -> None:
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with yaml_path.open("w", encoding="utf-8") as f:
        f.write(YAML_HEADER)
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def sync_json_from_yaml(yaml_path: Path, json_path: Path) -> dict:
    if not yaml_path.exists():
        raise SystemExit(f"{yaml_path} does not exist; generate the spec first with --from-csv (default mode).")
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default=None,
        help="Suite name (subfolder under inputs/; config is read from "
        "inputs/<suite>/connectivity-tests.toml if present). "
        "Falls back to the TEST_SUITE environment variable, and finally to "
        "the 'default' suite, if omitted.",
    )
    parser.add_argument("--inputs-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--yaml-out", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--port-protocol-pairing",
        choices=["one_to_one", "cross_product"],
        default=None,
        help="Overrides connectivity-tests.toml for this run.",
    )
    parser.add_argument(
        "--from-yaml",
        action="store_true",
        help="Does not re-read the CSVs: resyncs --json-out from --yaml-out (manual edits).",
    )
    args = parser.parse_args()

    suite = resolve_suite(args.suite)
    if args.inputs_dir is None:
        check_suite_exists(suite)
    inputs_dir = args.inputs_dir or ROOT / "inputs" / suite
    yaml_out = args.yaml_out or inputs_dir / "connectivity-test-spec.yaml"
    json_out = args.json_out or inputs_dir / "connectivity-test-spec.json"

    if args.from_yaml:
        data = sync_json_from_yaml(yaml_out, json_out)
        print(f"✅ Resynced {json_out} from {yaml_out} ({len(data.get('tests', []))} tests)")
        return

    config_path = resolve_config_path(args.config, suite)
    config = load_config(config_path)
    if args.port_protocol_pairing:
        config["port_protocol_pairing"] = args.port_protocol_pairing

    data = generate_from_csv(inputs_dir, config)
    write_outputs(data, yaml_out, json_out)

    n_tests = len(data["tests"])
    n_auto = sum(1 for t in data["tests"] if t["automatable"])
    n_manual = n_tests - n_auto
    n_unresolved = len(data["unresolved"])
    print(f"✅ Generated {yaml_out} and {json_out}")
    print(f"    {n_tests} test cases total")
    print(f"    {n_auto} automatable (local_cloud_domain -> remote_cloud_domain)")
    print(f"    {n_manual} manual (remote_cloud_domain -> local_cloud_domain)")
    unresolved_icon = "✅" if n_unresolved == 0 else "⚠️"
    print(f"  {unresolved_icon} {n_unresolved} rows in 'unresolved'")


if __name__ == "__main__":
    sys.exit(main())
