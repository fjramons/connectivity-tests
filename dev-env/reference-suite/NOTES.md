# `dev-local` reference suite

Synthetic, git-tracked test plan used only for local development (see
README.md "Local development environment"). Copied into `suites/dev-local/`
by `dev-env/suite.sh sync` -- **never edit `suites/dev-local/` directly**,
it's regenerated from here (and gitignored, like every other suite's CSVs).

## Placeholder tokens

The CSVs here use tokens instead of literal IPs, because the actual
addresses depend on the subnet Docker assigns to the `kind` network on each
developer's machine (see `discover_subnet_prefix()` in
`dev-env/lib/common.sh`). `dev-env/suite.sh sync` substitutes them:

| Token | Resolves to | What it is |
|---|---|---|
| `__DEV_ENV_LOCAL_SERVER_1__` | `<prefix>.200` | MetalLB pool address (Local-cloud-domain-owned mock server #1, `Servers.csv`) |
| `__DEV_ENV_LOCAL_SERVER_2__` | `<prefix>.201` | MetalLB pool address (Local-cloud-domain-owned mock server #2, `Servers.csv`) |
| `__DEV_ENV_TARGET_OPEN__` | `<prefix>.210` | fake Remote target with something listening (`dev-env/targets.sh`) |
| `__DEV_ENV_TARGET_REFUSED__` | `<prefix>.211` | fake Remote target with nothing listening |
| `__DEV_ENV_TARGET_FILTERED__` | `<prefix>.212` | fake Remote target with the port silently dropped (iptables) |
| `__DEV_ENV_TARGET_UNREACHABLE__` | `<prefix>.230` | no container claims this address at all |

## What each `Clients.csv` row exercises

All 8 rows are `local_cloud_domain_to_remote_cloud_domain` (automatable),
alternating `Source Type` between `K8s Cluster` (routed through
`src/run_via_kubectl.sh` against the dev-env kind cluster) and `VM` (routed
through the emulated VM container, `dev-env/vm.sh`), and alternating
protocol so both TCP and UDP paths run at least once per target:

| Destination | Port/Protocol | Source Type | Expected verdict |
|---|---|---|---|
| target-open | 5201/tcp | K8s Cluster | `PASS` |
| target-open | 5202/udp | VM | `UDP_SENT_HOST_REACHABLE` (UDP has no strong "delivered" signal even when open, see README's "Step-by-step manual diagnosis" appendix) |
| target-refused | 5301/tcp | K8s Cluster | `PORT_REFUSED_NETWORK_OPEN` |
| target-refused | 5302/udp | VM | `UDP_REFUSED_NETWORK_OPEN` |
| target-filtered | 5401/tcp | K8s Cluster | `PORT_CLOSED_HOST_REACHABLE` |
| target-filtered | 5402/udp | VM | `UDP_SENT_HOST_REACHABLE` |
| (unreachable) | 5501/tcp | K8s Cluster | `HOST_UNREACHABLE` |
| (unreachable) | 5502/udp | VM | `UDP_SENT_HOST_UNREACHABLE` |

## What `Servers.csv` exercises

2 rows, `remote_cloud_domain_to_local_cloud_domain` (not automatable --
always reported as `SKIPPED_MANUAL_TEST_REQUIRED` by `generate_report.py`,
same as in production). Their real purpose here is exercising
`src/generate_server_manifests.py` + `kubectl apply` against the MetalLB
pool: after `uv run src/generate_server_manifests.py --suite dev-local` and
applying `suites/dev-local/outputs/manifests/servers/local/*-k8s.yaml`, both
Services should get an `EXTERNAL-IP` from the pool (`<prefix>.200` /
`<prefix>.201`), not stay `<pending>`.

## Reminder: this validates tooling, not real firewall rules

kind's default CNI enforces no NetworkPolicies and there's no corporate
firewall in the loop -- a `PASS` here proves the scripts/manifests/kubectl
flow work correctly, not that a real firewall rule is open.
