#!/usr/bin/env bash
# Runs the Local cloud domain (K8s Cluster) -> Remote cloud domain connectivity tests from
# the lab PC, using kubectl with direct access to the cluster (without
# going through the jumphost) and the persistent pod deployed with
# manifests/netshoot-client-k8s.yaml.
#
# Usage:
#   kubectl apply -f manifests/netshoot-client-k8s.yaml   # once
#   src/run_via_kubectl.sh [namespace] [deployment-name]
#
# The resulting log is copied to outputs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.log
set -euo pipefail

NAMESPACE="${1:-default}"
DEPLOYMENT="${2:-netshoot-client}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC="$ROOT_DIR/inputs/connectivity-test-spec.json"
PROBE="$ROOT_DIR/src/run_probe.py"
PROBE_CONFIG="$ROOT_DIR/connectivity-tests.toml"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_LOG="$ROOT_DIR/outputs/local-cloud-domain-to-remote-cloud-domain-k8s-${TIMESTAMP}.log"

if [[ ! -f "$SPEC" ]]; then
  echo "❌ $SPEC does not exist." >&2
  echo "   Generate the spec first with: uv run src/generate_test_spec.py" >&2
  exit 1
fi

POD="$(kubectl -n "$NAMESPACE" get pod -l app="$DEPLOYMENT" -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$POD" ]]; then
  echo "❌ No pod found with label app=$DEPLOYMENT in namespace $NAMESPACE." >&2
  echo "   Apply it first: kubectl apply -f manifests/netshoot-client-k8s.yaml" >&2
  exit 1
fi

echo "Using pod $POD (namespace $NAMESPACE)..." >&2

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

echo "✅ Log copied to $OUT_LOG" >&2
