#!/usr/bin/env bash
# Lanza las pruebas de conectividad Domain 3 (K8s Cluster) -> Domain 2 desde
# el PC de laboratorio, usando kubectl con acceso directo al cluster (sin
# pasar por el jumphost) y el pod persistente desplegado con
# manifests/netshoot-client-k8s.yaml.
#
# Uso:
#   kubectl apply -f manifests/netshoot-client-k8s.yaml   # una sola vez
#   src/run_via_kubectl.sh [namespace] [nombre-deployment]
#
# El log resultante se copia a outputs/domain3-to-domain2-k8s-<timestamp>.log
set -euo pipefail

NAMESPACE="${1:-default}"
DEPLOYMENT="${2:-netshoot-client}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="$ROOT_DIR/inputs/connectivity-test-spec.json"
PROBE="$ROOT_DIR/src/run_probe.py"
PROBE_CONFIG="$ROOT_DIR/connectivity-tests.toml"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_LOG="$ROOT_DIR/outputs/domain3-to-domain2-k8s-${TIMESTAMP}.log"

if [[ ! -f "$SPEC" ]]; then
  echo "No existe $SPEC. Genera antes el spec con: uv run src/generate_test_spec.py" >&2
  exit 1
fi

POD="$(kubectl -n "$NAMESPACE" get pod -l app="$DEPLOYMENT" -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$POD" ]]; then
  echo "No se encontro ningun pod con label app=$DEPLOYMENT en el namespace $NAMESPACE." >&2
  echo "Aplica antes: kubectl apply -f manifests/netshoot-client-k8s.yaml" >&2
  exit 1
fi

echo "Usando pod $POD (namespace $NAMESPACE)..." >&2

kubectl -n "$NAMESPACE" cp "$PROBE" "$POD:/tmp/run_probe.py"
kubectl -n "$NAMESPACE" cp "$SPEC" "$POD:/tmp/spec.json"

CONFIG_ARGS=()
if [[ -f "$PROBE_CONFIG" ]]; then
  kubectl -n "$NAMESPACE" cp "$PROBE_CONFIG" "$POD:/tmp/connectivity-tests.toml"
  CONFIG_ARGS=(--config /tmp/connectivity-tests.toml)
fi

kubectl -n "$NAMESPACE" exec "$POD" -- \
  python3 /tmp/run_probe.py --spec /tmp/spec.json --filter-source-type "K8s Cluster" \
    --out /tmp/result.log "${CONFIG_ARGS[@]}"

mkdir -p "$ROOT_DIR/outputs"
kubectl -n "$NAMESPACE" cp "$POD:/tmp/result.log" "$OUT_LOG"

echo "Log copiado a $OUT_LOG" >&2
