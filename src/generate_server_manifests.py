#!/usr/bin/env python3
"""Genera manifests/servers/<slug>-k8s.yaml: un Deployment+Service por cada
destino unico (ip+puerto+protocolo) donde Domain 3 actua de SERVIDOR
(direction: domain2_to_domain3 en el spec).

Cada manifiesto usa la misma imagen nicolaka/netshoot:v0.15 como listener
(via socat) escuchando exactamente en el puerto de la app real, que aun no
esta desplegada -- para poder validar el firewall ya autorizado en ese
IP:puerto sin esperar a que la app real este lista.

Se ejecuta en el PC de desarrollo con `uv run src/generate_server_manifests.py`.
Solo usa la libreria estandar (no requiere PyYAML: los manifiestos se generan
como texto plano ya formateado).
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
# Servidor de prueba TEMPORAL para {ip}:{port}/{protocol} ({description}).
#
# Este manifiesto sustituye temporalmente a la app real de Domain 3 en este
# puerto -- NO lo despliegues a la vez que la app real (mismo Service/puerto).
#
# Si ya existe un Service real con esta IP de LoadBalancer ya reservada y
# autorizada en el firewall, ajusta el "selector" de ese Service para que
# apunte a "app: {app_label}" en vez de aplicar el Service de abajo: un
# Service nuevo obtendria una IP de LoadBalancer DISTINTA a la ya autorizada.
#
# Origen en el spec: {origin_summary}
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
        if t["direction"] != "domain2_to_domain3":
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
        desc = t["destination"].get("description") or t.get("protocol_label") or "sin descripcion"
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
        print(f"Generado {args.out_dir / filename}")

    print(f"\nTotal: {len(destinations)} manifiestos de servidor en {args.out_dir}")


if __name__ == "__main__":
    sys.exit(main())
