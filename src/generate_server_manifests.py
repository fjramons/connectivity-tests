#!/usr/bin/env python3
"""Generates manifests/servers/{local,remote}/<slug>-k8s.yaml: one
Deployment+Service per unique destination (ip+port+protocol), for both
directions in the spec:

- servers/local/  -- direction remote_cloud_domain_to_local_cloud_domain,
  where Local cloud domain acts as SERVER. Deploy these here (kubectl) so
  Remote cloud domain can validate the firewall rule before the real app
  exists.
- servers/remote/ -- direction local_cloud_domain_to_remote_cloud_domain,
  where Remote cloud domain acts as SERVER. We have no deploy access to
  that cluster: these manifests are meant to be handed off to the team
  responsible for it, so THEY deploy them.

Both use the same nicolaka/netshoot:v0.15 image as a listener (via socat)
on exactly the real app's port, which isn't deployed yet -- so the
firewall rule already authorized on that IP:port can be validated without
waiting for the real app to be ready.

Runs on the dev PC with `uv run src/generate_server_manifests.py`.
Only uses the standard library (doesn't require PyYAML: manifests are generated
as already-formatted plain text).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
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


LOCAL_MANIFEST_TEMPLATE = """\
# TEMPORARY test server for {ip}:{port}/{protocol} ({description}).
#
# This manifest temporarily replaces the real Local cloud domain app on this
# port -- do NOT deploy it at the same time as the real app (same Service/port).
#
# If a real Service already exists with this LoadBalancer IP already reserved
# and authorized in the firewall, adjust that Service's "selector" to
# point to "app: {app_label}" instead of applying the Service below: a
# new Service would fail to reserve the same IP if it's already taken.
#
# Deploy into the target namespace (see "namespace" in
# connectivity-tests.toml, currently "{namespace}"):
#   kubectl apply -f {deploy_dir}/{filename} -n {namespace}
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
  annotations:
    # Current MetalLB annotation (kept alongside spec.loadBalancerIP below
    # for compatibility with older MetalLB releases / other LB controllers).
    metallb.io/loadBalancerIPs: "{ip}"
spec:
  type: LoadBalancer
  loadBalancerIP: {ip}
  selector:
    app: {app_label}
  ports:
    - name: {app_label}
      port: {port}
      targetPort: {port}
      protocol: {k8s_proto}
"""

REMOTE_MANIFEST_TEMPLATE = """\
# TEMPORARY test server for {ip}:{port}/{protocol} ({description}).
#
# This mocks a Remote-cloud-domain app that isn't deployed yet, so the
# already-authorized firewall rule for this IP:port can be validated from
# Local cloud domain before the real app exists.
#
# *** This manifest is meant to be deployed in the REMOTE CLOUD DOMAIN
# CLUSTER, NOT here. We (dev PC / Local cloud domain) have no deploy access
# to that cluster: hand this file off to the team responsible for it (the
# same way other artifacts from this repo are shared, e.g. via OneDrive
# Web), and have them adjust the following before applying it:
#   - namespace: none is set here (we don't know the target namespace) --
#     add "-n <namespace>" (or a "namespace:" field) matching their cluster.
#   - Service type / LoadBalancer mechanism: the Service below shows a
#     MetalLB-style example (spec.loadBalancerIP + the
#     metallb.io/loadBalancerIPs annotation) purely as an illustration --
#     replace it with whatever LoadBalancer/Ingress/NodePort mechanism
#     their cluster actually uses to expose {ip}:{port} externally.
#   - If a real Service already exists with this IP already reserved and
#     authorized in the firewall, adjust that Service's "selector" to
#     point to "app: {app_label}" instead of applying the Service below.
#
# Once deployed there, run the automated local_cloud_domain_to_remote_cloud_domain
# tests from Local cloud domain against {ip}:{port} (see README.md section 4).
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
  annotations:
    # Example only -- MetalLB shown here, replace with whatever LoadBalancer/
    # Ingress/NodePort mechanism the Remote cloud domain cluster actually uses.
    metallb.io/loadBalancerIPs: "{ip}"
spec:
  type: LoadBalancer
  loadBalancerIP: {ip}
  selector:
    app: {app_label}
  ports:
    - name: {app_label}
      port: {port}
      targetPort: {port}
      protocol: {k8s_proto}
"""


def load_config(config_path: Path) -> dict:
    defaults = {"namespace": "default"}
    if config_path.exists():
        with config_path.open("rb") as f:
            defaults.update(tomllib.load(f))
    return defaults


def load_tests(spec_path: Path, suite: str) -> list[dict]:
    if not spec_path.exists():
        raise SystemExit(
            f"❌ Spec not found: {spec_path}\n"
            "   Generate it first with: uv run src/generate_test_spec.py "
            f"--suite {suite}"
        )
    with spec_path.open(encoding="utf-8") as f:
        spec = json.load(f)
    return spec.get("tests", [])


def unique_destinations(tests: list[dict], direction: str) -> dict[tuple[str, int, str], dict]:
    destinations: dict[tuple[str, int, str], dict] = {}
    for t in tests:
        if t["direction"] != direction:
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


def render_local_manifest(entry: dict, namespace: str, deploy_dir_display: str) -> str:
    app_label = slugify(entry["ip"], entry["port"])
    protocol = entry["protocol"]
    return LOCAL_MANIFEST_TEMPLATE.format(
        ip=entry["ip"],
        port=entry["port"],
        protocol=protocol,
        description="; ".join(sorted(entry["descriptions"])),
        app_label=app_label,
        filename=f"{app_label}-k8s.yaml",
        namespace=namespace,
        deploy_dir=deploy_dir_display,
        socat_proto="UDP" if protocol == "udp" else "TCP",
        k8s_proto="UDP" if protocol == "udp" else "TCP",
        origin_summary=", ".join(entry["origins"]),
    )


def render_remote_manifest(entry: dict) -> str:
    app_label = slugify(entry["ip"], entry["port"])
    protocol = entry["protocol"]
    return REMOTE_MANIFEST_TEMPLATE.format(
        ip=entry["ip"],
        port=entry["port"],
        protocol=protocol,
        description="; ".join(sorted(entry["descriptions"])),
        app_label=app_label,
        socat_proto="UDP" if protocol == "udp" else "TCP",
        k8s_proto="UDP" if protocol == "udp" else "TCP",
        origin_summary=", ".join(entry["origins"]),
    )


def write_manifests(destinations: dict, out_dir: Path, render) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for key, entry in destinations.items():
        ip, port, _protocol = key
        filename = f"{slugify(ip, port)}-k8s.yaml"
        (out_dir / filename).write_text(render(entry), encoding="utf-8")
        print(f"  ✅ {out_dir / filename}")


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default=None,
        help="Suite name (subfolder under inputs//outputs/; config is read from "
        "inputs/<suite>/connectivity-tests.toml if present). Falls back to the "
        "TEST_SUITE environment variable, and finally to the 'default' suite, if omitted.",
    )
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Base 'servers' directory; local/ and remote/ subfolders are created under it.",
    )
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    suite = resolve_suite(args.suite)
    if args.spec is None:
        check_suite_exists(suite)
    spec = args.spec or ROOT / "inputs" / suite / "connectivity-test-spec.json"
    out_dir = args.out_dir or ROOT / "outputs" / suite / "manifests" / "servers"
    local_out_dir = out_dir / "local"
    remote_out_dir = out_dir / "remote"
    config_path = resolve_config_path(args.config, suite)

    config = load_config(config_path)
    namespace = config.get("namespace", "default")

    tests = load_tests(spec, suite)
    local_destinations = unique_destinations(tests, "remote_cloud_domain_to_local_cloud_domain")
    remote_destinations = unique_destinations(tests, "local_cloud_domain_to_remote_cloud_domain")

    local_deploy_dir_display = display_path(local_out_dir)
    remote_deploy_dir_display = display_path(remote_out_dir)

    write_manifests(
        local_destinations,
        local_out_dir,
        lambda entry: render_local_manifest(entry, namespace, local_deploy_dir_display),
    )
    write_manifests(remote_destinations, remote_out_dir, render_remote_manifest)

    print()
    print(f"✅ Generated {len(local_destinations)} local server manifests in {local_out_dir}")
    print("    (mocks for remote_cloud_domain_to_local_cloud_domain destinations)")
    print(f"    Deploy each with: kubectl apply -f {local_deploy_dir_display}/<file> -n {namespace}")
    print()
    print(f"✅ Generated {len(remote_destinations)} remote server manifests in {remote_out_dir}")
    print("    (mocks for local_cloud_domain_to_remote_cloud_domain destinations)")
    print(
        f"    ⚠️  Not deployable from here: send {remote_deploy_dir_display}/ to the "
        "Remote-cloud-domain team so they deploy it in their own cluster"
    )


if __name__ == "__main__":
    sys.exit(main())
