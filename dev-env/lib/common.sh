# Shared helpers for dev-env/*.sh (local K8s cluster + VM emulation for
# developing this repo without access to the real Local cloud domain /
# Remote cloud domain environments). Sourced, not executed directly.
#
# Conventions mirrored from src/run_via_kubectl.sh: `set -euo pipefail` in
# the caller, ℹ️/✅/❌ prefixed messages on stderr.

DEV_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "$DEV_ENV_DIR/.." && pwd)"

# Isolated kubeconfig: never touch ~/.kube/config or any system/user
# kubeconfig. Every kind/kubectl call in dev-env/ must run with this
# exported (see dev-env/env.sh for the user-facing `source`-able version).
export KUBECONFIG="$DEV_ENV_DIR/.kubeconfig"

CLUSTER_NAME="conntest-dev"
KIND_DOCKER_NETWORK="kind"
SUBNET_PREFIX_FILE="$DEV_ENV_DIR/.subnet-prefix"
VM_CONTAINER_NAME="conntest-dev-vm"

# Namespace the generic netshoot-client manifest and the generated local
# server manifests are applied into. dev-env/reference-suite/connectivity-tests.toml
# also documents "default" for this same reason -- kept as one shared
# constant (rather than duplicated literals in cluster.sh/validate.sh)
# since bash has no TOML parser to read the suite's own namespace key.
DEV_ENV_NAMESPACE="default"

log_info() { echo "ℹ️  $*" >&2; }
log_ok()   { echo "✅ $*" >&2; }
log_err()  { echo "❌ $*" >&2; }

require_cmd() {
  local cmd="$1" hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_err "'$cmd' is not installed or not on PATH."
    echo "   $hint" >&2
    exit 1
  fi
}

# Common preflight for anything that touches Docker/kind/kubectl. Never
# installs anything itself -- only reports what's missing and how to get it,
# so the user installs it themselves.
preflight_docker() {
  require_cmd docker "Install Docker: https://docs.docker.com/engine/install/"
  if ! docker info >/dev/null 2>&1; then
    log_err "Docker daemon is not reachable (is it running? do you have permission?)."
    exit 1
  fi
}

preflight_kind_kubectl() {
  preflight_docker
  require_cmd kind "Install kind: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
  require_cmd kubectl "Install kubectl: https://kubernetes.io/docs/tasks/tools/#kubectl"
}

preflight_compose() {
  preflight_docker
  if ! docker compose version >/dev/null 2>&1; then
    log_err "'docker compose' (v2 plugin) is not available."
    echo "   Install it as part of Docker: https://docs.docker.com/compose/install/" >&2
    exit 1
  fi
}

# Creates the "kind" docker network if it doesn't exist yet, so
# dev-env/targets.sh (and dev-env/validate.sh --only vm) can discover a
# subnet and run without ever needing a real kind K8s cluster. `kind create
# cluster` itself creates this same network when missing and transparently
# reuses it when already present (this is what lets multiple kind clusters
# share one network) -- so creating it ourselves first doesn't conflict
# with a later `dev-env/cluster.sh up`.
ensure_kind_network() {
  if ! docker network inspect "$KIND_DOCKER_NETWORK" >/dev/null 2>&1; then
    log_info "Creating docker network '$KIND_DOCKER_NETWORK' (no kind cluster exists yet)..."
    docker network create "$KIND_DOCKER_NETWORK" >/dev/null
  fi
}

# Discovers the subnet Docker assigned to the "kind" network (shared across
# all kind clusters on this machine, not per-cluster) and derives a fixed
# /24 slice from it: <first-two-octets>.255.0/24. kind's own node containers
# get low addresses from the low end of the pool, so the top-of-range .255
# octet is a safe, deterministic place to carve out addresses for MetalLB's
# pool and the fake Remote-cloud-domain target containers without colliding
# with kind's own allocations. Creates the network first (see
# ensure_kind_network) if it doesn't exist yet, and requires it to be a
# /16, kind's default -- if Docker ever hands out something narrower, this
# aborts rather than guess.
discover_subnet_prefix() {
  ensure_kind_network
  # kind's network is dual-stack (IPv6 + IPv4): IPAM.Config entries aren't
  # guaranteed to list IPv4 first, so pick the entry that actually looks
  # like an IPv4 CIDR rather than assuming index 0.
  local cidr
  cidr="$(docker network inspect "$KIND_DOCKER_NETWORK" -f '{{range .IPAM.Config}}{{.Subnet}}{{"\n"}}{{end}}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$' | head -n1)"
  if [[ -z "$cidr" ]]; then
    log_err "No IPv4 subnet found on the '$KIND_DOCKER_NETWORK' docker network."
    exit 1
  fi
  local prefix_len="${cidr#*/}"
  if [[ -z "$prefix_len" || "$prefix_len" -gt 16 ]]; then
    log_err "Unexpected '$KIND_DOCKER_NETWORK' network layout ($cidr)."
    echo "   This tooling assumes kind's default /16 pool; a narrower network isn't" >&2
    echo "   supported without manual adjustment of dev-env/lib/common.sh." >&2
    exit 1
  fi
  local octet1 octet2
  octet1="$(cut -d. -f1 <<<"$cidr")"
  octet2="$(cut -d. -f2 <<<"$cidr")"
  local prefix="${octet1}.${octet2}.255"
  echo "$prefix" >"$SUBNET_PREFIX_FILE"
  echo "$prefix"
}

# Reads back the prefix written by discover_subnet_prefix. Used by scripts
# that need it but don't themselves create the kind network (targets.sh,
# suite.sh, vm.sh).
read_subnet_prefix() {
  if [[ ! -f "$SUBNET_PREFIX_FILE" ]]; then
    log_err "No dev-env subnet recorded yet ($SUBNET_PREFIX_FILE missing)."
    echo "   Run 'dev-env/cluster.sh up' first." >&2
    exit 1
  fi
  cat "$SUBNET_PREFIX_FILE"
}

# Fixed offsets within the discovered <prefix>.0/24:
#   .200-.209  MetalLB IPAddressPool (Local-cloud-domain-owned mock servers)
#   .210-.219  fake Remote-cloud-domain target containers
#   .230       deliberately unassigned (HOST_UNREACHABLE case)
metallb_pool_range() {
  local prefix="$1"
  echo "${prefix}.200-${prefix}.209"
}

# Parses -y/--yes out of "$@" into ASSUME_YES=1/0, echoing the remaining
# arguments (one per line) for the caller to re-collect.
ASSUME_YES=0
parse_yes_flag() {
  local remaining=()
  for arg in "$@"; do
    case "$arg" in
      -y|--yes) ASSUME_YES=1 ;;
      *) remaining+=("$arg") ;;
    esac
  done
  printf '%s\n' "${remaining[@]:-}"
}

# Wraps the body of an "up" command: on any failure, offers to run the given
# cleanup function (auto-confirmed under -y/--yes, otherwise asked
# interactively with "no" as the default) instead of silently leaving
# partially-created resources OR silently destroying state someone might
# want to inspect/resume. See README.md "Local development environment" /
# the plan's "Limpieza de entornos temporales" section for the rationale.
run_up_with_recovery() {
  local cleanup_fn="$1"
  shift
  local up_fn="$1"
  shift
  # shellcheck disable=SC2064
  trap "_on_up_error '$cleanup_fn' \$?" ERR
  "$up_fn" "$@"
  trap - ERR
}

_on_up_error() {
  local cleanup_fn="$1" exit_code="$2"
  trap - ERR
  log_err "Failure during provisioning (exit $exit_code)."
  if [[ "$ASSUME_YES" == "1" ]]; then
    log_info "-y given: cleaning up partially-created resources..."
    "$cleanup_fn" || true
  else
    read -r -p "Destroy the partially-created resources? [y/N] " reply </dev/tty || reply="n"
    if [[ "$reply" =~ ^[Yy]$ ]]; then
      "$cleanup_fn" || true
    else
      log_info "Leaving things as-is so you can inspect/debug. Re-run 'up' to retry" \
        "(it's idempotent), or 'down' later to clean up manually."
    fi
  fi
  exit "$exit_code"
}
