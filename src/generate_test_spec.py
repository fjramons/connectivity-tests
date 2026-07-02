#!/usr/bin/env python3
"""Genera inputs/connectivity-test-spec.{yaml,json} a partir de los CSV de inputs/.

Se ejecuta en el PC de desarrollo con `uv run src/generate_test_spec.py`.
Requiere PyYAML (declarado en pyproject.toml, instalado por `uv sync`).
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import itertools
import json
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUTS_DIR = ROOT / "inputs"
DEFAULT_CONFIG = ROOT / "connectivity-tests.toml"
DEFAULT_YAML_OUT = DEFAULT_INPUTS_DIR / "connectivity-test-spec.yaml"
DEFAULT_JSON_OUT = DEFAULT_INPUTS_DIR / "connectivity-test-spec.json"

SERVERS_CSV_GLOB = "*Servers at EC.3.csv"
CLIENTS_CSV_GLOB = "*Clients at EC.3.csv"

UDP_RE = re.compile(r"\budp\b", re.IGNORECASE)
IP_RANGE_RE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s*-\s*(\d+\.\d+\.\d+\.\d+)$")
PORT_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


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
        f"{len(ports)} puertos y {len(protocols)} protocolos no coinciden en cantidad; "
        "se generó el producto cruzado como fallback seguro.",
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
                    "IP de destino no encontrada/parseable"
                    if not dest_ips
                    else "Puerto no encontrado/parseable"
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

    automatable = direction == "domain3_to_domain2"
    cases = []
    for ip, (port, protocol_label) in itertools.product(dest_ips, pairs):
        protocol_label = protocol_label or "unknown"
        case = {
            "id": f"{'c' if direction == 'domain3_to_domain2' else 's'}{next(counter):04d}",
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
                "Domain 3 actua de servidor: esta prueba requiere ejecucion manual "
                "desde un cliente en Domain 2. Ver README.md."
            )
        cases.append(case)
    return cases


def parse_servers_csv(path: Path, pairing_mode: str, unresolved: list[dict], counter: itertools.count) -> list[dict]:
    """'Servers at EC.3.csv': Domain 3 is the server -> direction domain2_to_domain3."""
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
                direction="domain2_to_domain3",
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
    """'Clients at EC.3.csv': Domain 3 is the client -> direction domain3_to_domain2."""
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
                direction="domain3_to_domain2",
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
        raise SystemExit(f"No se encontró ningún CSV que coincida con {pattern!r} en {inputs_dir}")
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
# Especificacion de pruebas de conectividad Domain 2 <-> Domain 3 (EC.3).
# Generado por src/generate_test_spec.py -- editable a mano.
# Tras editar a mano, ejecutar `uv run src/generate_test_spec.py --from-yaml`
# para resincronizar connectivity-test-spec.json SIN volver a leer los CSV.
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
        raise SystemExit(f"No existe {yaml_path}; genera primero el spec con --from-csv (modo por defecto).")
    with yaml_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-dir", type=Path, default=DEFAULT_INPUTS_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--yaml-out", type=Path, default=DEFAULT_YAML_OUT)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument(
        "--port-protocol-pairing",
        choices=["one_to_one", "cross_product"],
        default=None,
        help="Sobreescribe connectivity-tests.toml para esta ejecucion.",
    )
    parser.add_argument(
        "--from-yaml",
        action="store_true",
        help="No relee los CSV: resincroniza --json-out a partir de --yaml-out (ediciones manuales).",
    )
    args = parser.parse_args()

    if args.from_yaml:
        data = sync_json_from_yaml(args.yaml_out, args.json_out)
        print(f"Resincronizado {args.json_out} a partir de {args.yaml_out} ({len(data.get('tests', []))} tests).")
        return

    config = load_config(args.config)
    if args.port_protocol_pairing:
        config["port_protocol_pairing"] = args.port_protocol_pairing

    data = generate_from_csv(args.inputs_dir, config)
    write_outputs(data, args.yaml_out, args.json_out)

    n_tests = len(data["tests"])
    n_auto = sum(1 for t in data["tests"] if t["automatable"])
    n_manual = n_tests - n_auto
    n_unresolved = len(data["unresolved"])
    print(
        f"Generado {args.yaml_out} y {args.json_out}: "
        f"{n_tests} casos ({n_auto} automatizables domain3->domain2, "
        f"{n_manual} manuales domain2->domain3), {n_unresolved} filas en 'unresolved'."
    )


if __name__ == "__main__":
    sys.exit(main())
