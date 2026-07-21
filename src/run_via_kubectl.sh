#!/usr/bin/env bash
# Runs the Local cloud domain (K8s Cluster) -> Remote cloud domain connectivity tests from
# the lab PC, using kubectl with direct access to the cluster (without
# going through the jumphost) and the persistent pod deployed with
# manifests/netshoot-client-k8s.yaml.
#
# Usage:
#   kubectl apply -f manifests/netshoot-client-k8s.yaml   # once
#   src/run_via_kubectl.sh [--suite <name>] [--namespace <ns>] [--deployment <name>]
#
# --suite falls back to the TEST_SUITE environment variable, and finally to
# the "default" suite, if neither is given.
# The resulting log is copied to suites/<suite>/outputs/logs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.log
# (plus a matching <timestamp>.json companion with structured per-test
# results, consumed by `uv run src/generate_report.py --suite <suite>`)
set -euo pipefail

SUITE="${TEST_SUITE:-}"
NAMESPACE="default"
DEPLOYMENT="netshoot-client"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --suite) SUITE="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --deployment) DEPLOYMENT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--suite <name>] [--namespace <ns>] [--deployment <name>]" >&2
      exit 0
      ;;
    *)
      echo "❌ Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$SUITE" ]]; then
  SUITE="default"
  echo "ℹ️  No --suite/TEST_SUITE given: using suite '$SUITE'." >&2
fi
if [[ ! "$SUITE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "❌ Invalid suite name '$SUITE': must be a plain name (letters/digits/./-/_ only)." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE_DIR="$ROOT_DIR/suites/$SUITE"
if [[ ! -d "$SUITE_DIR" ]]; then
  EXISTING="$(find "$ROOT_DIR/suites" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%f\n' | sort | paste -sd ', ' -)"
  echo "❌ Suite '$SUITE' not found: $SUITE_DIR does not exist." >&2
  echo "   Existing suites: ${EXISTING:-(none yet)}" >&2
  echo "   Create $SUITE_DIR/ with your CSVs, or pick an existing suite with" >&2
  echo "   --suite <name> / export TEST_SUITE=<name>." >&2
  exit 1
fi
SPEC="$ROOT_DIR/suites/$SUITE/connectivity-test-spec.json"
PROBE="$ROOT_DIR/src/run_probe.py"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_LOG="$ROOT_DIR/suites/$SUITE/outputs/logs/local-cloud-domain-to-remote-cloud-domain-k8s-${TIMESTAMP}.log"

SUITE_CONFIG="$ROOT_DIR/suites/$SUITE/connectivity-tests.toml"
GENERIC_CONFIG="$ROOT_DIR/connectivity-tests.toml"
CONFIG_TEMPLATE="$ROOT_DIR/connectivity-tests.toml.template"
if [[ -f "$SUITE_CONFIG" ]]; then
  PROBE_CONFIG="$SUITE_CONFIG"
else
  if [[ ! -f "$GENERIC_CONFIG" && -f "$CONFIG_TEMPLATE" ]]; then
    cp "$CONFIG_TEMPLATE" "$GENERIC_CONFIG"
    echo "ℹ️  Created $GENERIC_CONFIG from $CONFIG_TEMPLATE (no $SUITE_CONFIG found)" >&2
  fi
  PROBE_CONFIG="$GENERIC_CONFIG"
fi

if [[ ! -f "$SPEC" ]]; then
  echo "❌ $SPEC does not exist." >&2
  echo "   Generate the spec first with: uv run src/generate_test_spec.py --suite $SUITE" >&2
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
  python3 /tmp/run_probe.py batch --spec /tmp/spec.json --filter-source-type "K8s Cluster" \
    --out /tmp/result.log "${CONFIG_ARGS[@]}"

mkdir -p "$ROOT_DIR/suites/$SUITE/outputs/logs"
kubectl -n "$NAMESPACE" cp "$POD:/tmp/result.log" "$OUT_LOG"
OUT_RESULTS="${OUT_LOG%.log}.json"
kubectl -n "$NAMESPACE" cp "$POD:/tmp/result.json" "$OUT_RESULTS"

echo "✅ Log copied to $OUT_LOG" >&2
echo "✅ Structured results copied to $OUT_RESULTS" >&2
echo "" >&2
echo "run_probe.py, spec.json and connectivity-tests.toml are still in /tmp inside" >&2
echo "the pod (it's a persistent Deployment, not ephemeral) -- for ad hoc single-case" >&2
echo "tests, without re-running the whole battery, open a shell there:" >&2
echo "  kubectl exec -it $POD -n $NAMESPACE -- bash" >&2
echo "  python3 /tmp/run_probe.py list --spec /tmp/spec.json" >&2
echo "  python3 /tmp/run_probe.py tcp <ip> <port>" >&2
echo "  python3 /tmp/run_probe.py udp <ip> <port> --config /tmp/connectivity-tests.toml" >&2
echo "See README.md section 4.2 for details." >&2
