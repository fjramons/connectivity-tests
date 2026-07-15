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
import sys
import tomllib
from pathlib import Path

from suite_common import ROOT, check_suite_exists, resolve_config_path, resolve_suite


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
{service_annotations}spec:
  type: LoadBalancer
{service_loadbalancer_ip}  selector:
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
#     MetalLB-style example (spec.loadBalancerIP and/or the
#     metallb.io/loadBalancerIPs annotation, per this suite's
#     metallb_ip_mechanism config -- see connectivity-tests.toml) purely as
#     an illustration -- replace it with whatever LoadBalancer/Ingress/
#     NodePort mechanism their cluster actually uses to expose {ip}:{port}
#     externally.
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
{service_annotations}spec:
  type: LoadBalancer
{service_loadbalancer_ip}  selector:
    app: {app_label}
  ports:
    - name: {app_label}
      port: {port}
      targetPort: {port}
      protocol: {k8s_proto}
"""


METALLB_IP_MECHANISMS = ("both", "annotation", "spec_field")


def load_config(config_path: Path) -> dict:
    defaults = {"namespace": "default", "metallb_ip_mechanism": "both"}
    if config_path.exists():
        with config_path.open("rb") as f:
            defaults.update(tomllib.load(f))
    return defaults


def build_service_ip_fields(ip: str, mechanism: str) -> tuple[str, str]:
    """Returns the (possibly empty) "annotations:" block and "loadBalancerIP:"
    line for the Service, per metallb_ip_mechanism -- current MetalLB releases
    reject a Service that sets both spec.loadBalancerIP and the
    metallb.io/loadBalancerIPs annotation at once ("service can not have
    both"), while older releases needed one or the other depending on
    version, hence this being configurable rather than hardcoded."""
    if mechanism not in METALLB_IP_MECHANISMS:
        raise SystemExit(
            f"❌ Invalid metallb_ip_mechanism {mechanism!r}: must be one of {METALLB_IP_MECHANISMS}."
        )
    annotations = ""
    if mechanism in ("both", "annotation"):
        annotations = f'  annotations:\n    metallb.io/loadBalancerIPs: "{ip}"\n'
    loadbalancer_ip = ""
    if mechanism in ("both", "spec_field"):
        loadbalancer_ip = f"  loadBalancerIP: {ip}\n"
    return annotations, loadbalancer_ip


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


def render_local_manifest(entry: dict, namespace: str, deploy_dir_display: str, mechanism: str) -> str:
    app_label = slugify(entry["ip"], entry["port"])
    protocol = entry["protocol"]
    annotations, loadbalancer_ip = build_service_ip_fields(entry["ip"], mechanism)
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
        service_annotations=annotations,
        service_loadbalancer_ip=loadbalancer_ip,
    )


def render_remote_manifest(entry: dict, mechanism: str) -> str:
    app_label = slugify(entry["ip"], entry["port"])
    protocol = entry["protocol"]
    annotations, loadbalancer_ip = build_service_ip_fields(entry["ip"], mechanism)
    return REMOTE_MANIFEST_TEMPLATE.format(
        ip=entry["ip"],
        port=entry["port"],
        protocol=protocol,
        description="; ".join(sorted(entry["descriptions"])),
        app_label=app_label,
        socat_proto="UDP" if protocol == "udp" else "TCP",
        k8s_proto="UDP" if protocol == "udp" else "TCP",
        origin_summary=", ".join(entry["origins"]),
        service_annotations=annotations,
        service_loadbalancer_ip=loadbalancer_ip,
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
    parser.add_argument(
        "--metallb-ip-mechanism",
        choices=METALLB_IP_MECHANISMS,
        default=None,
        help="Overrides connectivity-tests.toml's metallb_ip_mechanism for this run "
        "(current MetalLB releases reject a Service that sets both spec.loadBalancerIP "
        "and the metallb.io/loadBalancerIPs annotation; older releases needed one or the "
        "other depending on version -- pick whichever your target cluster's MetalLB needs).",
    )
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
    mechanism = args.metallb_ip_mechanism or config.get("metallb_ip_mechanism", "both")

    tests = load_tests(spec, suite)
    local_destinations = unique_destinations(tests, "remote_cloud_domain_to_local_cloud_domain")
    remote_destinations = unique_destinations(tests, "local_cloud_domain_to_remote_cloud_domain")

    local_deploy_dir_display = display_path(local_out_dir)
    remote_deploy_dir_display = display_path(remote_out_dir)

    write_manifests(
        local_destinations,
        local_out_dir,
        lambda entry: render_local_manifest(entry, namespace, local_deploy_dir_display, mechanism),
    )
    write_manifests(
        remote_destinations,
        remote_out_dir,
        lambda entry: render_remote_manifest(entry, mechanism),
    )

    print()
    print(f"ℹ️  metallb_ip_mechanism: {mechanism}")
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
