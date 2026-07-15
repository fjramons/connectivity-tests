# Source this (do NOT execute it) to point your own shell's plain kubectl
# (and src/run_via_kubectl.sh, which relies on the ambient kubectl context)
# at the local dev-env kind cluster instead of any real kubeconfig you may
# already have configured:
#
#   source dev-env/env.sh
#
# This only exports KUBECONFIG for the current shell session -- it never
# writes to or merges with ~/.kube/config or any other kubeconfig on your
# system. Open a new shell (or unset KUBECONFIG) to go back to whatever you
# had before.
_DEV_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export KUBECONFIG="$_DEV_ENV_DIR/.kubeconfig"
unset _DEV_ENV_DIR
echo "ℹ️  KUBECONFIG set to $KUBECONFIG for this shell (dev-env kind cluster)." >&2
