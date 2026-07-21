#!/usr/bin/env bash
# Runs the VM-sourced (source.type == "VM") automated connectivity tests
# against the emulated VM container (dev-env/vm.sh), the dev-env equivalent
# of src/run_via_kubectl.sh for the K8s-Cluster-sourced cases. Mirrors that
# script's structure (kubectl cp/exec -> docker cp/exec here) -- this is
# the automation gap flagged in the create-local-vm skill's manual recipe.
#
# Requires dev-env/vm.sh up to have run first.
#
# Usage:
#   dev-env/run-vm-tests.sh [--suite dev-local]
#
# The resulting log is copied to
# suites/<suite>/outputs/logs/local-cloud-domain-to-remote-cloud-domain-vm-emulated-<timestamp>.log
# (plus the matching .json companion) -- "vm-emulated" distinguishes this
# script's runs from a real jumphost's own log naming, while still merging
# correctly into generate_report.py's per-test-id view.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

SUITE="dev-local"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --suite) SUITE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--suite <name>]" >&2
      exit 0
      ;;
    *)
      log_err "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if ! docker ps --format '{{.Names}}' | grep -qx "$VM_CONTAINER_NAME"; then
  log_err "Emulated VM container '$VM_CONTAINER_NAME' is not running."
  echo "   Run 'dev-env/vm.sh up' first." >&2
  exit 1
fi

SUITE_DIR="$ROOT_DIR/suites/$SUITE"
SPEC="$SUITE_DIR/connectivity-test-spec.json"
if [[ ! -f "$SPEC" ]]; then
  log_err "$SPEC does not exist."
  echo "   Generate it first with: uv run src/generate_test_spec.py --suite $SUITE" >&2
  echo "   (or dev-env/suite.sh sync --suite $SUITE if this is the dev-local suite)" >&2
  exit 1
fi

SUITE_CONFIG="$SUITE_DIR/connectivity-tests.toml"
GENERIC_CONFIG="$ROOT_DIR/connectivity-tests.toml"
CONFIG_TEMPLATE="$ROOT_DIR/connectivity-tests.toml.template"
if [[ -f "$SUITE_CONFIG" ]]; then
  PROBE_CONFIG="$SUITE_CONFIG"
else
  if [[ ! -f "$GENERIC_CONFIG" && -f "$CONFIG_TEMPLATE" ]]; then
    cp "$CONFIG_TEMPLATE" "$GENERIC_CONFIG"
    log_info "Created $GENERIC_CONFIG from $CONFIG_TEMPLATE (no $SUITE_CONFIG found)"
  fi
  PROBE_CONFIG="$GENERIC_CONFIG"
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_LOG="$SUITE_DIR/outputs/logs/local-cloud-domain-to-remote-cloud-domain-vm-emulated-${TIMESTAMP}.log"

log_info "Using container $VM_CONTAINER_NAME..."
docker cp "$ROOT_DIR/src/run_probe.py" "$VM_CONTAINER_NAME:/tmp/run_probe.py"
docker cp "$SPEC" "$VM_CONTAINER_NAME:/tmp/spec.json"

CONFIG_ARGS=()
if [[ -f "$PROBE_CONFIG" ]]; then
  docker cp "$PROBE_CONFIG" "$VM_CONTAINER_NAME:/tmp/connectivity-tests.toml"
  CONFIG_ARGS=(--config /tmp/connectivity-tests.toml)
fi

docker exec "$VM_CONTAINER_NAME" \
  python3 /tmp/run_probe.py batch --spec /tmp/spec.json --filter-source-type VM \
    --out /tmp/result.log "${CONFIG_ARGS[@]}"

mkdir -p "$SUITE_DIR/outputs/logs"
docker cp "$VM_CONTAINER_NAME:/tmp/result.log" "$OUT_LOG"
OUT_RESULTS="${OUT_LOG%.log}.json"
docker cp "$VM_CONTAINER_NAME:/tmp/result.json" "$OUT_RESULTS"

log_ok "Log copied to $OUT_LOG"
log_ok "Structured results copied to $OUT_RESULTS"
