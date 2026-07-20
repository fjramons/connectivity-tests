#!/usr/bin/env bash
# Local K8s cluster emulation (kind + MetalLB) for developing/testing this
# repo's generators and kubectl automation (src/generate_server_manifests.py,
# src/run_via_kubectl.sh) without needing the real Local cloud domain
# cluster. See README.md "Local development environment".
#
# Usage:
#   dev-env/cluster.sh up [-y|--yes]
#   dev-env/cluster.sh deploy-client
#   dev-env/cluster.sh down [-y|--yes]
#   dev-env/cluster.sh status
#
# Never touches ~/.kube/config or any system/user kubeconfig: all kind/kubectl
# calls here use an isolated KUBECONFIG (dev-env/.kubeconfig, gitignored) --
# see dev-env/lib/common.sh and dev-env/env.sh (to point your own shell's
# kubectl at this cluster too, e.g. for src/run_via_kubectl.sh).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

METALLB_MANIFEST="$DEV_ENV_DIR/kind/vendor/metallb-native.yaml"
POOL_TEMPLATE="$DEV_ENV_DIR/kind/ipaddresspool.yaml.template"
KIND_CONFIG="$DEV_ENV_DIR/kind/kind-cluster.yaml"
CLIENT_MANIFEST="$ROOT_DIR/manifests/netshoot-client-k8s.yaml"

cmd_up() {
  preflight_kind_kubectl

  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    log_info "Cluster '$CLUSTER_NAME' already exists, skipping creation."
  else
    log_info "Creating kind cluster '$CLUSTER_NAME' (KUBECONFIG=$KUBECONFIG)..."
    kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG"
  fi

  local prefix
  prefix="$(discover_subnet_prefix)"
  log_info "Using subnet prefix ${prefix}.0/24 (pool ${prefix}.200-${prefix}.209, fake targets ${prefix}.210-${prefix}.219, unreachable ${prefix}.230)."

  if kubectl get ns metallb-system >/dev/null 2>&1; then
    log_info "MetalLB already installed, skipping."
  else
    log_info "Installing MetalLB (vendored $(basename "$METALLB_MANIFEST"))..."
    kubectl apply -f "$METALLB_MANIFEST"
    log_info "Waiting for MetalLB to be ready..."
    kubectl -n metallb-system wait --for=condition=Available --timeout=180s deployment/controller
    kubectl -n metallb-system rollout status --timeout=180s daemonset/speaker
  fi

  log_info "Applying MetalLB IPAddressPool/L2Advertisement for ${prefix}.200-${prefix}.209..."
  DEV_ENV_SUBNET_PREFIX="$prefix" envsubst '${DEV_ENV_SUBNET_PREFIX}' <"$POOL_TEMPLATE" | kubectl apply -f -

  log_ok "Cluster '$CLUSTER_NAME' is up. kubectl context: $(kubectl config current-context)"
  log_info "Run 'source dev-env/env.sh' in your shell so plain kubectl / src/run_via_kubectl.sh use it too."
  log_info "Next: dev-env/cluster.sh deploy-client (if you need the test client), dev-env/targets.sh up, then dev-env/suite.sh sync --suite dev-local."
}

cmd_deploy_client() {
  preflight_kind_kubectl

  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    log_err "Cluster '$CLUSTER_NAME' does not exist yet."
    echo "   Run 'dev-env/cluster.sh up' first." >&2
    exit 1
  fi

  log_info "Applying the generic netshoot client manifest (manifests/netshoot-client-k8s.yaml) in namespace $DEV_ENV_NAMESPACE..."
  kubectl apply -n "$DEV_ENV_NAMESPACE" -f "$CLIENT_MANIFEST"
  kubectl -n "$DEV_ENV_NAMESPACE" rollout status --timeout=120s deployment/netshoot-client
  log_ok "Test client deployed in namespace $DEV_ENV_NAMESPACE."
}

cmd_down() {
  if ! command -v kind >/dev/null 2>&1; then
    log_info "kind not installed, nothing to tear down."
    return 0
  fi
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    log_info "Deleting kind cluster '$CLUSTER_NAME'..."
    kind delete cluster --name "$CLUSTER_NAME"
  else
    log_info "Cluster '$CLUSTER_NAME' does not exist, nothing to do."
  fi
  rm -f "$SUBNET_PREFIX_FILE" "$KUBECONFIG"
  log_ok "Cluster torn down."
  log_info "Note: the shared 'kind' docker network stays until no kind cluster (from any project) remains."
}

cmd_status() {
  if ! command -v kind >/dev/null 2>&1 || ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
    log_info "Cluster '$CLUSTER_NAME' is NOT running."
    return 0
  fi
  log_ok "Cluster '$CLUSTER_NAME' exists. kubectl context: $(kubectl config current-context 2>/dev/null || echo '?')"
  kubectl get nodes 2>&1 || true
  echo "---" >&2
  kubectl -n metallb-system get pods 2>&1 || log_info "metallb-system namespace not found."
  echo "---" >&2
  kubectl -n metallb-system get ipaddresspools.metallb.io 2>&1 || log_info "No IPAddressPool found."
  if [[ -f "$SUBNET_PREFIX_FILE" ]]; then
    log_info "Recorded subnet prefix: $(cat "$SUBNET_PREFIX_FILE")"
  fi
}

main() {
  local subcommand="${1:-}"
  [[ $# -gt 0 ]] && shift
  parse_yes_flag "$@" >/dev/null # sets the global ASSUME_YES

  case "$subcommand" in
    up)             run_up_with_recovery cmd_down cmd_up ;;
    deploy-client)  cmd_deploy_client ;;
    down)           cmd_down ;;
    status)         cmd_status ;;
    *)
      echo "Usage: $0 {up|deploy-client|down|status} [-y|--yes]" >&2
      exit 1
      ;;
  esac
}

main "$@"
