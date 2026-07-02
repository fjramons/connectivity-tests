#!/usr/bin/env python3
"""Generates manifests/servers/<slug>-k8s.yaml: one Deployment+Service per
unique destination (ip+port+protocol) where Local cloud domain acts as SERVER
(direction: remote_cloud_domain_to_local_cloud_domain in the spec).

Each manifest uses the same nicolaka/netshoot:v0.15 image as a listener
(via socat) listening on exactly the real app's port, which isn't
deployed yet -- so the firewall rule already authorized on that
IP:port can be validated without waiting for the real app to be ready.

Runs on the dev PC with `uv run src/generate_server_manifests.py`.
Only uses the standard library (doesn't require PyYAML: manifests are generated
as already-formatted plain text).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT / "inputs" / "connectivity-test-spec.json"
DEFAULT_OUT_DIR = ROOT / "manifests" / "servers"

MANIFEST_TEMPLATE = """\
# TEMPORARY test server for {ip}:{port}/{protocol} ({description}).
#
# This manifest temporarily replaces the real Local cloud domain app on this
# port -- do NOT deploy it at the same time as the real app (same Service/port).
#
# If a real Service already exists with this LoadBalancer IP already reserved
# and authorized in the firewall, adjust that Service's "selector" to
# point to "app: {app_label}" instead of applying the Service below: a
# new Service would get a DIFFERENT LoadBalancer IP from the one already authorized.
#
# Origin in the spec: {origin_summary}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app_label}
  labels:
    app: {app_label}
    purpose: connectivity-test-server
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {app_label}
  template:
    metadata:
      labels:
        app: {app_label}
        purpose: connectivity-test-server
    spec:
      containers:
        - name: netshoot
          image: nicolaka/netshoot:v0.15
          command: ["sh", "-c"]
          args:
            - "socat -v {socat_proto}-LISTEN:{port},fork,reuseaddr EXEC:'/bin/cat'"
---
apiVersion: v1
kind: Service
metadata:
  name: {app_label}
  labels:
    app: {app_label}
    purpose: connectivity-test-server
spec:
  type: LoadBalancer
  selector:
    app: {app_label}
  ports:
    - name: {app_label}
      port: {port}
      targetPort: {port}
      protocol: {k8s_proto}
"""


def load_tests(spec_path: Path) -> list[dict]:
    with spec_path.open(encoding="utf-8") as f:
        spec = json.load(f)
    return spec.get("tests", [])


def unique_destinations(tests: list[dict]) -> dict[tuple[str, int, str], dict]:
    destinations: dict[tuple[str, int, str], dict] = {}
    for t in tests:
        if t["direction"] != "remote_cloud_domain_to_local_cloud_domain":
            continue
        key = (t["destination"]["ip"], t["port"], t["protocol"])
        entry = destinations.setdefault(
            key,
            {
                "ip": t["destination"]["ip"],
                "port": t["port"],
                "protocol": t["protocol"],
                "descriptions": set(),
                "origins": [],
            },
        )
        desc = t["destination"].get("description") or t.get("protocol_label") or "no description"
        entry["descriptions"].add(desc)
        entry["origins"].append(f"{t['origin']['file']}#{t['origin']['row']}")
    return destinations


def slugify(ip: str, port: int) -> str:
    return f"conntest-{ip.replace('.', '-')}-{port}"


def render_manifest(entry: dict) -> str:
    app_label = slugify(entry["ip"], entry["port"])
    protocol = entry["protocol"]
    return MANIFEST_TEMPLATE.format(
        ip=entry["ip"],
        port=entry["port"],
        protocol=protocol,
        description="; ".join(sorted(entry["descriptions"])),
        app_label=app_label,
        socat_proto="UDP" if protocol == "udp" else "TCP",
        k8s_proto="UDP" if protocol == "udp" else "TCP",
        origin_summary=", ".join(entry["origins"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    tests = load_tests(args.spec)
    destinations = unique_destinations(tests)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for key, entry in destinations.items():
        ip, port, protocol = key
        filename = f"{slugify(ip, port)}-k8s.yaml"
        (args.out_dir / filename).write_text(render_manifest(entry), encoding="utf-8")
        print(f"Generated {args.out_dir / filename}")

    print(f"\nTotal: {len(destinations)} server manifests in {args.out_dir}")


if __name__ == "__main__":
    sys.exit(main())
