---
name: create-local-k8s-cluster
description: Brings up (or tears down) a local kind + MetalLB Kubernetes cluster emulating the "Local cloud domain K8s cluster" for developing/testing this repo without real Lab PC access. Use when validating changes to generate_server_manifests.py, run_via_kubectl.sh, or manifests/netshoot-client-k8s.yaml against a real cluster locally.
---
# Create a local K8s cluster (kind + MetalLB)

This emulates the Lab PC's Kubernetes cluster on the developer's own
laptop, so that `src/generate_server_manifests.py`'s output and
`src/run_via_kubectl.sh` can be exercised against a real cluster with real
`LoadBalancer` Services, without needing the real Local cloud domain
environment. See `README.md` "Local development environment" for the full
picture (including why kind+MetalLB was chosen over k3d's built-in
Klipper LB: it lets the existing manifest generator run completely
unmodified).

**Important**: this validates the tooling/automation (manifests, kubectl,
`run_probe.py`), not real firewall rules -- kind enforces no
NetworkPolicies and there's no corporate firewall in the loop. A `PASS`
here proves the scripts work, not that a real firewall rule is open.

## Prerequisites (one-time, manual -- never auto-installed by this skill)

- `kind` (https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- `kubectl` (https://kubernetes.io/docs/tasks/tools/#kubectl)
- Docker, running and reachable (`docker info` succeeds)

`dev-env/cluster.sh up` checks for all three and stops with clear
instructions if any is missing -- it never installs anything itself.

## What it does NOT touch

Every kind/kubectl call here uses an isolated `KUBECONFIG`
(`dev-env/.kubeconfig`, gitignored) -- **your `~/.kube/config` or any other
system/user kubeconfig is never read or modified**. To point your own
shell's plain `kubectl` (or `src/run_via_kubectl.sh`, which relies on the
ambient kubectl context) at this cluster, run `source dev-env/env.sh` in
that shell.

## Usage

```bash
dev-env/cluster.sh up              # create the cluster, install MetalLB, apply the pool
dev-env/cluster.sh deploy-client   # apply the netshoot test client (separate, explicit step)
dev-env/cluster.sh status          # read-only: cluster/nodes/MetalLB/pool state
dev-env/cluster.sh down            # tear everything down (cluster only -- see below for the rest)
```

`up` is idempotent (safe to re-run). Add `-y`/`--yes` to auto-confirm
cleanup if provisioning fails partway through instead of being asked
interactively (default: asks, defaulting to "no" so you can inspect/debug
a partial failure instead of losing state).

## What `up` does, step by step

1. Preflight: `kind`, `kubectl`, `docker info`.
2. Creates the kind cluster `conntest-dev` (`dev-env/kind/kind-cluster.yaml`,
   single node) if it doesn't already exist.
3. Discovers the subnet Docker assigned to the shared `kind` docker network
   and derives a fixed `<prefix>.0/24` slice for this tooling's own use
   (`.200-.209` MetalLB pool, `.210-.219` fake Remote targets, `.230`
   deliberately unassigned) -- recorded in `dev-env/.subnet-prefix` for
   `dev-env/targets.sh` and `dev-env/suite.sh` to read later.
4. Installs the vendored MetalLB manifest
   (`dev-env/kind/vendor/metallb-native.yaml`, pinned so this works
   offline after the initial clone) and waits for it to be ready.
5. Applies the `IPAddressPool`/`L2Advertisement` for `.200-.209`.

## What `deploy-client` does

Applies `manifests/netshoot-client-k8s.yaml` (the existing, unmodified
client manifest) into namespace `default` and waits for its rollout, so
`src/run_via_kubectl.sh --suite dev-local` has something to talk to. Kept
as its own explicit step, separate from `up`, rather than bundled
silently into cluster creation -- run it whenever you actually need the
test client, not automatically every time the cluster comes up. Requires
`up` to have run first (errors clearly otherwise).

## Verifying it worked

```bash
source dev-env/env.sh
kubectl get nodes                                  # one Ready node
kubectl -n metallb-system get pods                 # controller + speaker Running
kubectl -n metallb-system get ipaddresspools.metallb.io
kubectl get deploy netshoot-client                 # 1/1 ready (after 'deploy-client')
```

To see a `LoadBalancer` Service actually get an `EXTERNAL-IP` from the
pool (rather than `<pending>`), generate and apply a server manifest
against the `dev-local` suite (see the `create-local-vm` skill and
`dev-env/reference-suite/NOTES.md` for the full synthetic suite this is
meant to be paired with):

```bash
uv run src/generate_test_spec.py --suite dev-local
uv run src/generate_server_manifests.py --suite dev-local
kubectl apply -f suites/dev-local/outputs/manifests/servers/local/<file>-k8s.yaml -n default
kubectl get svc -n default   # EXTERNAL-IP should be <prefix>.200 or .201, not <pending>
```

## Tearing down

`dev-env/cluster.sh down` deletes the kind cluster and the recorded
kubeconfig/subnet files. Note: the shared `kind` docker network itself
(used by every kind cluster on the machine, not just this one) is only
removed by `kind` once no kind cluster remains at all -- this is expected,
not a bug. Don't forget `dev-env/targets.sh down` and `dev-env/vm.sh down`
too if you brought those up as part of the same session.
