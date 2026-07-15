#!/usr/bin/env bash
# Local VM/jumphost emulation for developing/testing run_probe.py and the
# standalone-script flow (src/generate_standalone_script.py) without a real
# Remote-cloud-domain-reachable jumphost. See README.md "Local development
# environment" for the two ways to validate the VM path:
#
#   "emulated" (this script): a persistent container standing in for the
#   jumphost, joined to the same docker network as the fake Remote-cloud-domain
#   targets (dev-env/targets.sh) and/or the kind cluster (dev-env/cluster.sh) --
#   run_probe.py runs directly inside it, no nested `docker run --network host`
#   needed.
#
#   "real": run the actual outputs/<suite>/standalone/*.sh generated script
#   directly on this same laptop -- it already only needs Docker
#   (`docker run --network host ...`), so the laptop itself already acts as
#   the jumphost for that artifact. See the skill for the caveat about
#   `--network host` on Docker Desktop/WSL2.
#
# Usage:
#   dev-env/vm.sh up [-y|--yes]
#   dev-env/vm.sh down [-y|--yes]
#   dev-env/vm.sh status
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

IMAGE="nicolaka/netshoot:v0.15"

cmd_up() {
  preflight_docker

  if docker ps -a --format '{{.Names}}' | grep -qx "$VM_CONTAINER_NAME"; then
    log_info "Container '$VM_CONTAINER_NAME' already exists."
    if [[ "$(docker inspect -f '{{.State.Running}}' "$VM_CONTAINER_NAME")" != "true" ]]; then
      docker start "$VM_CONTAINER_NAME" >/dev/null
    fi
  else
    log_info "Creating emulated VM container '$VM_CONTAINER_NAME' ($IMAGE)..."
    # No extra --cap-add: Docker's default capability set already includes
    # NET_RAW, enough for ping/traceroute (same as manifests/netshoot-client-k8s.yaml,
    # which requests no special capabilities either).
    docker run -d --name "$VM_CONTAINER_NAME" "$IMAGE" sleep infinity >/dev/null
  fi

  if docker network inspect "$KIND_DOCKER_NETWORK" >/dev/null 2>&1; then
    if docker network inspect "$KIND_DOCKER_NETWORK" -f '{{range .Containers}}{{.Name}} {{end}}' | grep -qw "$VM_CONTAINER_NAME"; then
      log_info "Already connected to the '$KIND_DOCKER_NETWORK' network."
    else
      log_info "Connecting to the '$KIND_DOCKER_NETWORK' docker network (cluster/fake targets reachability)..."
      docker network connect "$KIND_DOCKER_NETWORK" "$VM_CONTAINER_NAME"
    fi
  else
    log_info "Docker network '$KIND_DOCKER_NETWORK' doesn't exist yet (no cluster up) -- VM container" \
      "is still usable standalone (public IPs, or bring up dev-env/cluster.sh and dev-env/targets.sh" \
      "first and re-run 'up' to join it)."
  fi

  log_ok "Emulated VM '$VM_CONTAINER_NAME' is up."
  docker exec "$VM_CONTAINER_NAME" python3 --version >&2 || true
  log_info "Example manual run (mirrors src/run_via_kubectl.sh's kubectl cp/exec, via docker instead):"
  echo "  docker cp src/run_probe.py $VM_CONTAINER_NAME:/tmp/run_probe.py" >&2
  echo "  docker cp inputs/dev-local/connectivity-test-spec.json $VM_CONTAINER_NAME:/tmp/spec.json" >&2
  echo "  docker exec $VM_CONTAINER_NAME python3 /tmp/run_probe.py batch --spec /tmp/spec.json \\" >&2
  echo "    --filter-source-type VM --out /tmp/result.log" >&2
  echo "  docker cp $VM_CONTAINER_NAME:/tmp/result.log outputs/dev-local/logs/vm-emulated-\$(date -u +%Y%m%dT%H%M%SZ).log" >&2
}

cmd_down() {
  if docker ps -a --format '{{.Names}}' | grep -qx "$VM_CONTAINER_NAME"; then
    log_info "Removing container '$VM_CONTAINER_NAME'..."
    docker rm -f "$VM_CONTAINER_NAME" >/dev/null
  else
    log_info "Container '$VM_CONTAINER_NAME' does not exist, nothing to do."
  fi
  log_ok "Emulated VM torn down."
}

cmd_status() {
  if ! docker ps -a --format '{{.Names}}' | grep -qx "$VM_CONTAINER_NAME"; then
    log_info "Emulated VM '$VM_CONTAINER_NAME' is NOT running."
    return 0
  fi
  docker ps -a --filter "name=^${VM_CONTAINER_NAME}\$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' >&2
  echo "---" >&2
  docker inspect -f 'Networks: {{range $k, $v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}' "$VM_CONTAINER_NAME" >&2
}

main() {
  local subcommand="${1:-}"
  [[ $# -gt 0 ]] && shift
  parse_yes_flag "$@" >/dev/null # sets the global ASSUME_YES

  case "$subcommand" in
    up)     run_up_with_recovery cmd_down cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    *)
      echo "Usage: $0 {up|down|status} [-y|--yes]" >&2
      exit 1
      ;;
  esac
}

main "$@"
