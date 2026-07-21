#!/usr/bin/env bash
# Combined orchestration for dev-env: brings up whichever infra is needed,
# regenerates the spec/manifests, deploys/runs the automated tests, and
# produces the consolidated report -- one command instead of manually
# sequencing dev-env/{cluster,targets,vm,suite}.sh plus the four
# src/generate_*.py generators plus src/run_via_kubectl.sh, as described in
# README.md "Local development environment".
#
# Usage:
#   dev-env/validate.sh run    [--suite dev-local] [--only k8s|vm|both] [-y|--yes]
#   dev-env/validate.sh down   [--suite dev-local] [-y|--yes]
#   dev-env/validate.sh status [--suite dev-local]
#
# --only k8s: cluster.sh + targets.sh only (skips the emulated VM container
#             and the VM-sourced test battery).
# --only vm:  targets.sh + vm.sh only (skips the kind cluster entirely --
#             targets.sh creates the "kind" docker network itself if no
#             cluster exists yet, see ensure_kind_network() in lib/common.sh).
# --only both (default): everything.
#
# `run` deliberately leaves all infra running afterwards -- this is meant
# for iterative development, not a one-shot CI check. Tear down explicitly
# with `dev-env/validate.sh down` once the change being validated is done.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

SUITE="dev-local"
ONLY="both"

cmd_run() {
  local yes_flag=()
  [[ "$ASSUME_YES" == "1" ]] && yes_flag=(-y)

  case "$ONLY" in
    k8s)  preflight_kind_kubectl ;;
    vm)   preflight_docker ;;
    both) preflight_kind_kubectl; preflight_docker ;;
  esac
  preflight_compose

  log_info "=== Bringing up infra (--only $ONLY) ==="
  "$DEV_ENV_DIR/targets.sh" up "${yes_flag[@]}"
  if [[ "$ONLY" != "vm" ]]; then
    "$DEV_ENV_DIR/cluster.sh" up "${yes_flag[@]}"
    "$DEV_ENV_DIR/cluster.sh" deploy-client
  fi
  if [[ "$ONLY" != "k8s" ]]; then
    "$DEV_ENV_DIR/vm.sh" up "${yes_flag[@]}"
  fi

  log_info "=== Syncing suite '$SUITE' from dev-env/reference-suite/ ==="
  "$DEV_ENV_DIR/suite.sh" sync --suite "$SUITE"

  log_info "=== Generating test spec ==="
  (cd "$ROOT_DIR" && uv run src/generate_test_spec.py --suite "$SUITE")

  if [[ "$ONLY" != "vm" ]]; then
    log_info "=== Generating and applying server manifests (namespace $DEV_ENV_NAMESPACE) ==="
    (cd "$ROOT_DIR" && uv run src/generate_server_manifests.py --suite "$SUITE")
    kubectl apply -n "$DEV_ENV_NAMESPACE" -f "$ROOT_DIR/suites/$SUITE/outputs/manifests/servers/local/"
  fi

  if [[ "$ONLY" != "vm" ]]; then
    log_info "=== Running K8s-Cluster-sourced tests ==="
    bash "$ROOT_DIR/src/run_via_kubectl.sh" --suite "$SUITE"
  fi

  if [[ "$ONLY" != "k8s" ]]; then
    log_info "=== Running VM-sourced tests (emulated) ==="
    "$DEV_ENV_DIR/run-vm-tests.sh" --suite "$SUITE"
  fi

  log_info "=== Generating consolidated report ==="
  (cd "$ROOT_DIR" && uv run src/generate_report.py --suite "$SUITE")

  log_ok "Validation run complete for suite '$SUITE'."
  echo "   Report: suites/$SUITE/outputs/logs/summary-report.txt (and .html)" >&2
  echo >&2
  log_info "Infra is still running (by design, for continued iteration)."
  log_info "When the development being validated is done, tear it down with:"
  echo "     dev-env/validate.sh down --suite $SUITE" >&2
}

cmd_down() {
  local yes_flag=()
  [[ "$ASSUME_YES" == "1" ]] && yes_flag=(-y)
  "$DEV_ENV_DIR/vm.sh" down "${yes_flag[@]}"
  "$DEV_ENV_DIR/targets.sh" down "${yes_flag[@]}"
  "$DEV_ENV_DIR/cluster.sh" down "${yes_flag[@]}"
  log_ok "Everything torn down."
}

cmd_status() {
  echo "=== Cluster ===" >&2
  "$DEV_ENV_DIR/cluster.sh" status
  echo "=== Fake Remote-cloud-domain targets ===" >&2
  "$DEV_ENV_DIR/targets.sh" status
  echo "=== Emulated VM ===" >&2
  "$DEV_ENV_DIR/vm.sh" status
  echo "=== Suite '$SUITE' ===" >&2
  if [[ -d "$ROOT_DIR/suites/$SUITE" ]]; then
    log_info "suites/$SUITE/ exists. Re-run 'dev-env/suite.sh sync --suite $SUITE' if dev-env/reference-suite/ changed since."
  else
    log_info "suites/$SUITE/ has not been synced yet (dev-env/suite.sh sync --suite $SUITE)."
  fi
}

main() {
  local subcommand="${1:-}"
  [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --suite) SUITE="$2"; shift 2 ;;
      --only) ONLY="$2"; shift 2 ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      *)
        log_err "Unknown argument: $1"
        exit 1
        ;;
    esac
  done
  case "$ONLY" in
    k8s|vm|both) ;;
    *)
      log_err "Invalid --only '$ONLY': must be 'k8s', 'vm', or 'both'."
      exit 1
      ;;
  esac

  case "$subcommand" in
    run)    cmd_run ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    *)
      echo "Usage: $0 {run|down|status} [--suite <name>] [--only k8s|vm|both] [-y|--yes]" >&2
      exit 1
      ;;
  esac
}

main "$@"
