# Connectivity tests Local cloud domain ⟷ Remote cloud domain

Tools to systematically validate connectivity and the firewall rules
open between "Remote cloud domain" and "Local cloud domain",
based on the rule matrix in `inputs/<suite>/*.csv`.

Multiple independent **test suites** (e.g. different firewall matrix
versions or environments) can coexist on disk, each in its own named
subfolder. Every command below takes a suite name via `--suite <name>`
(or `src/run_via_kubectl.sh`'s equivalent flag), which can also be set
once per shell session with `export TEST_SUITE=<name>` instead of
repeating `--suite` on every command — the same value is used
consistently everywhere (`inputs/<name>/`,
`outputs/<name>/manifests/servers/{local,remote}/`,
`outputs/<name>/standalone/`, `outputs/<name>/logs/`, and optionally
`inputs/<name>/connectivity-tests.toml` if that suite needs its own config
override). If neither `--suite` nor `TEST_SUITE` is given, the `default`
suite (`inputs/default/`) is used automatically, with a printed notice —
so a stray or forgotten `--suite` doesn't silently point at some other
suite unnoticed. If the resolved suite's `inputs/<name>/` folder doesn't
exist, every command fails fast with a clear error listing the suites
that do exist. `ls inputs/` lists the suites that currently exist.

## Quickstart

The whole flow is three steps:

1. Turn your firewall rule matrix into a spec.
2. Run the automated tests, and
3. Build the report.

If you want to **test in a real environment with your own firewall rule matrix**, just drop
your two CSVs (`*Clients*.csv`/`*Servers*.csv`) into `inputs/default/`, or to
`inputs/<suite>/` for a named suite (to draft your CSVs, you may want to use the
ready-made template at `dev-env/reference-suite/*.csv` for the expected columns).

Then, run:

```bash
# 1. CSVs -> spec
uv run src/generate_test_spec.py

# 2. Deploy the test client (one-time), then run the tests
kubectl apply -f manifests/netshoot-client-k8s.yaml
src/run_via_kubectl.sh

# 3. Build and view the report
uv run src/generate_report.py
xdg-open outputs/default/logs/summary-report.html    # macOS: use `open`
```

No `--suite` flag needed above (defaults to the `default` suite; add
`--suite <name>` for a named one instead). Details:
[section 1](#1-generate-the-test-specification) (spec) ·
[section 3](#3-automation-local-cloud-domain--remote-cloud-domain)
(running tests; manual/mock-server variants:
[section 2](#2-mock-test-servers-for-destinations-that-dont-exist-yet-both-directions),
[section 4](#4-manual-tests-as-a-client-in-local-cloud-domain)) ·
[section 5](#5-consolidated-summary--html-report) (reports).

Want to try that same flow with the **bundled example in a locally emulated
environment** first — no real CSVs or network access needed? Spin up an
emulated K8s cluster and VM/jumphost (all in Docker — see
["Local development environment"](#local-development-environment) for what
this sets up), then run the exact same three steps against it:

```bash
# 0. Spin up the emulated environment (K8s cluster + VM/jumphost), point
#    kubectl at it, and sync the bundled example plan
dev-env/cluster.sh up && dev-env/targets.sh up && dev-env/vm.sh up
source dev-env/env.sh
dev-env/suite.sh sync

# 1. CSVs -> spec
uv run src/generate_test_spec.py --suite dev-local

# 2. Deploy the test client (one-time), then run the tests
kubectl apply -f manifests/netshoot-client-k8s.yaml
src/run_via_kubectl.sh --suite dev-local

# 3. Build and view the report
uv run src/generate_report.py --suite dev-local
xdg-open outputs/dev-local/logs/summary-report.html    # macOS: use `open`

# 4. Tear down the emulated environment
dev-env/vm.sh down && dev-env/targets.sh down && dev-env/cluster.sh down
```

The only differences from the real-environment commands above are:

- **Step 0:** Bring up the emulated environment, point `kubectl` at it, and sync the
bundled plan instead of dropping in your own CSVs
- **Step 4:** Tear the environment down

Steps 1-3, including the client-deploy line itself, are identical (`--suite dev-local` aside).

**NOTE:** For demonstration purposes, all of steps 0-4 above with the `dev-local` suite can also run in one shot with:

```bash
dev-env/validate.sh run
dev-env/validate.sh down
```

## The three environments

This project moves across three environments with very different capabilities:

| Environment | Network access | What happens there |
| --- | --- | --- |
| **Dev PC** | No access to Local cloud domain | Generate the spec, the K8s manifests, and the self-contained script, with `uv` |
| **Lab PC** | Direct `kubectl` to the Local cloud domain clusters, and access to the jumphost (SSH) | Apply K8s manifests, run the automation against clusters, open a session to the jumphost |
| **Jumphost / VM in Local cloud domain** | Direct access to Remote cloud domain, but transferring files is hard | Paste the self-contained script generated on the dev PC, or use Docker Compose manually |

Artifacts generated on the dev PC (`inputs/<suite>/connectivity-test-spec.*`,
`outputs/<suite>/manifests/servers/local/`, `outputs/<suite>/standalone/`) are
moved to the lab PC manually via via not automatable means. From
there:

- Everything related to **K8s** (`kubectl apply` / `exec` / `cp`) runs
  directly from the lab PC.
- Everything related to **VM/Docker** runs by opening a session to the
  jumphost and pasting the `standalone/` script there (self-contained: no
  `scp` required).

`outputs/<suite>/manifests/servers/remote/` follows a different handoff:
we have no deploy access to the Remote cloud domain cluster, so those
manifests are instead sent directly to the team responsible
for that cluster, not to the lab PC, in a process that is not automatable either.

## Prerequisites and installation (dev PC)

- [`uv`](https://docs.astral.sh/uv/) installed:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- Python managed by `uv` (no need to install it separately):

  ```bash
  # Optional
  uv python install 3.12
  ```

- Create the virtual environment with the dependencies (`pyyaml`) declared
  in `pyproject.toml`:

  ```bash
  uv sync
  ```

- Docker (optional, only if you want to test the `nicolaka/netshoot:v0.15`
  image locally before taking it to Local cloud domain -- also used by the
  local dev environment below).

If you want to validate changes end-to-end locally (a real K8s cluster
with `LoadBalancer` Services, and a VM/jumphost stand-in) instead of only
generating artifacts, see "Local development environment" further down —
it needs `kind`, `kubectl`, and `docker compose` in addition to the above.

Prerequisites in the other environments:

- **Lab PC**: `kubectl` configured with context to the Local
  cloud domain clusters; SSH (or other) access to the jumphost; access to
  OneDrive Web to receive the artifacts.
- **Jumphost / Local cloud domain VM**: Docker + Docker Compose installed, with
  network egress to Remote cloud domain on the ports to validate.

## Repository structure

```text
inputs/<suite>/                          Source CSVs + generated spec (readable YAML + JSON for the runner)
inputs/<suite>/connectivity-tests.toml   Optional per-suite config override (only needed if a suite's
                                          namespace/pairing/probe calibration differs from the generic default)
connectivity-tests.toml                  Generic/default config, used by any suite without its own override;
                                          gitignored -- auto-created from connectivity-tests.toml.template if missing
connectivity-tests.toml.template         Git-tracked template for both of the above
src/                                      Scripts (generators on the dev PC, stdlib-only runner)
manifests/                                CLIENT (netshoot) manifests, suite-independent, always at the root
outputs/<suite>/manifests/servers/local/  SERVER mocks for remote_cloud_domain_to_local_cloud_domain destinations
                                           (deploy in Local cloud domain, via kubectl from the lab PC)
outputs/<suite>/manifests/servers/remote/ SERVER mocks for local_cloud_domain_to_remote_cloud_domain destinations
                                           (hand off to the Remote-cloud-domain team; they deploy them)
outputs/<suite>/standalone/               Self-contained script(s) to paste into the jumphost/VM
outputs/<suite>/logs/                     Run logs: one .log + structured .json per run, plus the
                                           consolidated summary-report.{txt,html} (section 5)
dev-env/                                  Local K8s cluster + VM emulation for development (see
                                           "Local development environment" below); dev-env/reference-suite/
                                           is the git-tracked synthetic test plan synced into inputs/dev-local/
```

## 1. Generate the test specification

From the two CSVs in `inputs/<suite>/` (put your CSVs there first — create
the folder if the suite is new):

```bash
uv run src/generate_test_spec.py --suite cne2.0-v0.22
# or, with TEST_SUITE exported once per shell session:
export TEST_SUITE=cne2.0-v0.22
uv run src/generate_test_spec.py
```

This generates `inputs/<suite>/connectivity-test-spec.yaml` (readable, hand-editable) and its
twin `inputs/<suite>/connectivity-test-spec.json` (the one the runner actually reads,
with no dependency on PyYAML inside Local cloud domain).

Each CSV expands into individual test cases (one IP × one port), including
lists (`10.2.113.129, 10.2.113.131`) and ranges (`10.180.141.99-10.180.141.105`).
When ports and protocols have the same number of elements in a row
(e.g. 3 ports and 3 protocols), the pairing is controlled from
`inputs/<suite>/connectivity-tests.toml` (`port_protocol_pairing`: `one_to_one` by default,
or `cross_product`); it can also be forced for a single run with
`--port-protocol-pairing cross_product`.

The config file also has a `namespace` key (`"default"` unless set) that
`src/generate_server_manifests.py` uses to print/document the suggested
`kubectl apply -n <namespace>` command for the server manifests (see
section 2) — it is not embedded into the generated YAML.

Config resolution: `inputs/<suite>/connectivity-tests.toml` if that suite
has its own override, else the generic `connectivity-tests.toml` at the
repo root (auto-created from the git-tracked
`connectivity-tests.toml.template` the first time any script needs it and
it's missing). Most suites don't need their own override — only create
`inputs/<suite>/connectivity-tests.toml` if that suite's namespace, port/
protocol pairing, or UDP probe calibration should differ from the generic
default.

If you edit the YAML by hand (for example, to annotate or fix a case in
`unresolved`), resync only the JSON without re-reading the CSVs:

```bash
uv run src/generate_test_spec.py --suite cne2.0-v0.22 --from-yaml
```

Each test case indicates `direction` (`local_cloud_domain_to_remote_cloud_domain`
if Local cloud domain acts as client, `remote_cloud_domain_to_local_cloud_domain`
if it acts as server) and `automatable` (only `true` for
`local_cloud_domain_to_remote_cloud_domain`, which is the only thing we can run without
depending on someone in Remote cloud domain doing something).

## 2. Mock test servers for destinations that don't exist yet (both directions)

Some destinations on both sides already belong to real apps that aren't
deployed yet. To validate the firewall rule without waiting for those apps
to be ready, generate a mock listener manifest **for each unique
destination** (same IP:port as the real app) in either direction with a
single command:

```bash
uv run src/generate_server_manifests.py --suite cne2.0-v0.22
```

This writes `outputs/cne2.0-v0.22/manifests/servers/local/<slug>-k8s.yaml`
(one per unique `remote_cloud_domain_to_local_cloud_domain` destination) and
`outputs/cne2.0-v0.22/manifests/servers/remote/<slug>-k8s.yaml` (one per
unique `local_cloud_domain_to_remote_cloud_domain` destination) in the same
run — see 2.1 and 2.2 below for what to do with each.

Both kinds of manifest deploy the same `nicolaka/netshoot:v0.15` container
acting as a listener (`socat`) on the exact port of the real app, with its
own `Service type: LoadBalancer` that requests the real app's IP explicitly
(`spec.loadBalancerIP` plus the `metallb.io/loadBalancerIPs` annotation, for
compatibility with both older and current MetalLB). **Do not deploy either
kind together with the real app** on the same port — if a real `Service`
already exists with that LoadBalancer IP already reserved and authorized in
the firewall, edit the manifest's `Service` (or the real one's selector) to
avoid a conflict; the generated YAML itself includes this warning as a
comment. Both TCP and UDP destinations are supported (the `socat` command
and the Service's `protocol` field switch automatically based on the
destination's protocol).

`manifests/netshoot-client-k8s.yaml` and
`manifests/netshoot-client-docker-compose.yml` (section 4.1) are **not**
suite-specific: they're generic client tooling with no embedded spec data,
and always stay at the `manifests/` root regardless of which suite you're
testing.

### 2.1 Servers in Local cloud domain (`servers/local/`)

The `remote_cloud_domain_to_local_cloud_domain` destinations (Spotfire,
Vertica/Olap DB, IAM/Keycloak, CMM Ingress) are real Local-cloud-domain apps
not deployed yet, and we control the cluster they'll run on — deploy the
generated mock directly:

```bash
kubectl apply -f outputs/cne2.0-v0.22/manifests/servers/local/<slug>-k8s.yaml -n <namespace>
```

`<namespace>` comes from the `namespace` key in the resolved config file
(see "Config resolution" in section 1; `"default"` unless set) — it is not
baked into the manifest, deploy explicitly with `-n` for clarity; the
generator also prints this same command with the configured namespace
filled in, and each manifest's header comment repeats it.

Once deployed, ask someone in Remote cloud domain to test with
common Linux tools (see the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools)
appendix for the rationale behind each one — same
tools, same TCP/UDP logic, just run from the Remote cloud domain side):

```bash
nc -zv 10.11.119.182 443
curl -v https://10.11.119.182:443
openssl s_client -connect 10.11.119.180:5433
nmap -p 443,8443 10.11.119.178
ping -c4 10.11.119.182
traceroute 10.11.119.182
```

If none of these tests work, don't assume the firewall is
misconfigured: it could be that the test service hasn't been deployed, or that
the `Service` is using a different LoadBalancer IP than the authorized one. The
["Step-by-step manual diagnosis"](#step-by-step-manual-diagnosis-no-scripts)
appendix explains, step by step, how to distinguish both cases with the same
tools (`nc`, `ping`, `traceroute`) used above.

### 2.2 Servers in Remote cloud domain (`servers/remote/`)

The `local_cloud_domain_to_remote_cloud_domain` destinations (e.g. Netcool
UDP 1167) are real Remote-cloud-domain apps not deployed yet either, but
**we have no deploy access to that cluster**. These manifests are meant to
be **handed off** to the team responsible for the Remote-cloud-domain
cluster (the same out-of-band channel used for other artifacts, e.g.
OneDrive Web), for them to deploy in their own cluster. Each manifest's
header comment says so explicitly and lists what they need to fill in
themselves (namespace, and whatever LoadBalancer/Ingress/NodePort mechanism
their cluster actually uses — the `Service` shown uses MetalLB only as an
example, since we don't know their infrastructure).

Once the Remote-cloud-domain team has deployed a mock, **no manual step is
needed on our side**: this direction is already automatable, so the
existing automated client tests (section 3) will exercise it directly from
Local cloud domain — there's no need to ask anyone to run `nc`/`curl` by
hand, unlike section 2.1's flow.

## 3. Automation Local cloud domain → Remote cloud domain

Only this direction is automated (Local cloud domain acts as client), because
it's the one we can run without depending on someone in Remote cloud domain doing
something. The test engine (`src/run_probe.py`) only uses the Python
standard library: it attempts a TCP connection or a UDP send, and if it fails (or always, for
UDP, which doesn't confirm delivery) it runs `ping`/`tcptraceroute` as complementary
diagnostics to distinguish "host unreachable" from "port/service down but
host alive". `remote_cloud_domain_to_local_cloud_domain` cases are logged as
`SKIPPED_MANUAL_TEST_REQUIRED`
(see section 2). Besides the verdicts, the log includes additional signals
that help interpret a failure without having to repeat it by hand: if a TCP
failure came with an explicit "no route to host"/"network unreachable" instead of a
silent timeout, it's noted as such; and after a `traceroute`/`tcptraceroute`
a summary sentence is added indicating how far the traffic reached and whether
it reached the destination itself or not (see the ["Step-by-step manual diagnosis"](#step-by-step-manual-diagnosis-no-scripts)
appendix for the detail on how this is interpreted).
Every diagnostic step also gets an explicit `COMMAND:` line in the log
showing exactly what was run (the real `ping`/`tcptraceroute` invocation
including flags; for the TCP connect/UDP send themselves, which are raw
socket calls rather than a CLI tool, a clearly-labeled equivalent showing
the exact ip/port/timeout/ICMP-margin parameters used) — so the log alone
is enough to reproduce a result by hand without guessing what was
actually launched.

Alongside the human-readable `.log`, each `run_probe.py batch` run also
writes a structured `.json` companion (same basename) with one record per
test: verdict, a short comment for weak/inconclusive verdicts, the exact
commands used, and the full diagnostic detail. `generate_report.py` (see
section 5) consumes these to build a consolidated summary.

The wait margin used in the UDP ICMP detection trick (see
the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools) appendix, UDP section) is computed from the RTT measured by `ping` to that
same host, and is configurable in the resolved config file's `[probe]`
table if the default values don't fit the real Local cloud domain →
Remote cloud domain latency; both backends below embed/copy it
automatically.

### K8s backend (from the lab PC)

```bash
kubectl apply -f manifests/netshoot-client-k8s.yaml   # once
src/run_via_kubectl.sh --suite cne2.0-v0.22 [--namespace <ns>] [--deployment <name>]
```

`--suite` falls back to `$TEST_SUITE` if omitted, same as the Python
scripts. The script does a `kubectl cp` of `run_probe.py`, the spec, and
the resolved config file (see "Config resolution" in section 1) to the pod, runs it with
`kubectl exec ... run_probe.py batch ...`, and copies both the resulting
log and its `.json` companion to
`outputs/<suite>/logs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.{log,json}`.
All from the lab PC, without going through the jumphost. Those copied files
stay in the pod's `/tmp` afterward, ready for the ad hoc `list`/`tcp`/`udp`
subcommands from section 4.2.

### VM/jumphost backend (self-contained script)

On the dev PC, generate the script (it embeds the `run_probe.py` code,
the spec subset with `source.type == "VM"`, and the resolved config file):

```bash
uv run src/generate_standalone_script.py --suite cne2.0-v0.22
```

This creates `outputs/cne2.0-v0.22/standalone/local-cloud-domain-to-remote-cloud-domain-vm-tests.sh`. Take it
to the lab PC (OneDrive Web), open an SSH session to the jumphost/VM from there, and
**paste the file's entire content** into the terminal (no `scp` needed: the
script writes its own temp files locally and only needs
Docker installed). When the batch finishes, it prints the log delimited by
`===== LOG START =====` / `===== LOG END =====`, followed by its `.json`
companion delimited by `===== RESULTS_JSON START =====` / `===== RESULTS_JSON END =====`:
copy both blocks and save them as
`outputs/cne2.0-v0.22/logs/local-cloud-domain-to-remote-cloud-domain-vm-tests-<timestamp>.log`
and the matching `...-<timestamp>.json`
on the lab PC. It then drops you into an interactive shell with
`run_probe.py` and the spec still present at `/data`, for the ad hoc
`list`/`tcp`/`udp` subcommands from section 4.2 — `exit` when done to clean
up the temp files.

## 4. Manual tests as a client in Local cloud domain

### 4.1 Deploy the client

#### On a K8s cluster

```bash
kubectl apply -f manifests/netshoot-client-k8s.yaml -n <namespace>   # Namespace that allows privileged containers
kubectl exec -it deploy/netshoot-client -n <namespace> -- bash
```

#### On a VM (Docker Compose)

```bash
docker compose -f manifests/netshoot-client-docker-compose.yml up -d
docker compose -f manifests/netshoot-client-docker-compose.yml exec netshoot bash
```

(`network_mode: host` makes traffic leave with the VM's own IP.)

Both give you a shell with the tools (`nicolaka/netshoot:v0.15`) used in
section 4.2 below and the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools) appendix.

### 4.2 High-level: `run_probe.py` subcommands (recommended)

`run_probe.py` — the same engine that drives the automated battery (section
3) — can also be invoked for a single case at a time, following the exact
same diagnosis methodology (TCP: connect, then `ping`/`tcptraceroute` if it
fails; UDP: `ping` first to calibrate the ICMP margin, then the double-send
trick), but printing the full detail straight to the terminal instead of
only to a log file.

- **`list`**: prints the automatable cases known from a spec (id,
  destination IP, port, protocol), so you know what to test:

  ```bash
  python3 run_probe.py list --spec spec.json
  ```

- **`tcp <ip> <port>`** / **`udp <ip> <port>`**: run a single case by hand:

  ```bash
  python3 run_probe.py tcp 10.180.141.111 443
  python3 run_probe.py udp 10.45.66.48 1167 --config connectivity-tests.toml   # --config optional, calibrates the ICMP margin (inputs/<suite>/connectivity-tests.toml if that suite has its own override)
  ```

  `udp` does **not** require `ping`/ICMP to succeed: it always attempts the
  send regardless, falling back to a default wait margin if `ping` fails
  (see the UDP note in the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools) appendix).

- `--help` works at every level: `run_probe.py --help`, `run_probe.py tcp --help`, etc.

Where to find `run_probe.py` + `spec.json` already in place, without extra
file transfers:

- **K8s**: after running `src/run_via_kubectl.sh` at least once (section 3),
  both files remain in `/tmp` inside the pod (a persistent Deployment, not
  an ephemeral job) — `kubectl exec -it deploy/netshoot-client -n <namespace> -- bash`
  and run the commands above against `/tmp/run_probe.py --spec /tmp/spec.json`.
  If you haven't run the battery yet, `kubectl cp` them in yourself the same
  way the script does.
- **VM/jumphost**: pasting the self-contained script (section 3) already
  ends by dropping you into an interactive shell with both files at
  `/data` — just run the commands above there.

If any of these commands fails, don't stop at "it doesn't work": see the
["Step-by-step manual diagnosis"](#step-by-step-manual-diagnosis-no-scripts)
appendix (and the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools)
appendix for the exact commands) to know whether it's a firewall problem or
simply that the Remote cloud domain service isn't deployed yet.

## 5. Consolidated summary & HTML report

Once the logs from both backends (K8s and/or VM) for a suite are collected
under `outputs/<suite>/logs/`, run, on the dev PC:

```bash
uv run src/generate_report.py --suite cne2.0-v0.22
```

This merges every `.json` companion found there (by test id, the most
recently-run result wins when a test was run more than once) against the
suite's spec, so every test case shows up exactly once even though the K8s
and VM backends each only cover the subset of tests originating from their
own `source.type`. It writes two views of the same data:

- `outputs/<suite>/logs/summary-report.txt` — a plain-text table (id,
  direction, name, verdict, comment, log pointer) for quick terminal/text
  viewing.
- `outputs/<suite>/logs/summary-report.html` — a single self-contained
  HTML file, color-coded by verdict severity, with each row expandable to
  show the full diagnostic detail (including the `COMMAND:` lines) without
  needing to open the raw `.log` separately.

Besides the verdicts `run_probe.py` itself can produce (see "Verdict
interpretation" below), the report adds one status of its own,
`NOT_RUN_YET` (❔): an automatable test with no matching result in any
`.json` file yet — distinct from `SKIPPED_MANUAL_TEST_REQUIRED`, which
means the test genuinely can't be automated (Local cloud domain acts as
server, see section 2).

## Local development environment

The Dev PC has no network access to either Local cloud domain or Remote
cloud domain (see "The three environments" above), so validating a change
end-to-end normally requires the real Lab PC and jumphost. `dev-env/`
emulates both physical capabilities **locally**, entirely inside Docker,
so features can be exercised realistically while developing:

- a local Kubernetes cluster with real `LoadBalancer` Services (`kind` +
  MetalLB), standing in for the Lab PC's cluster;
- a local VM/jumphost stand-in, standing in for the Remote-cloud-domain-
  reachable jumphost.

**Important — this validates tooling, not real firewall rules.** kind's
default CNI enforces no NetworkPolicies and there's no corporate firewall
in the loop. A `PASS` here proves the generators/manifests/kubectl/
`run_probe.py` flow works correctly end-to-end, not that a real firewall
rule is open in Local cloud domain or Remote cloud domain.

### Prerequisites (one-time, manual — nothing here installs anything for you)

- [`kind`](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- [`kubectl`](https://kubernetes.io/docs/tasks/tools/#kubectl)
- Docker with the `docker compose` v2 plugin (same Docker as above)

Every `dev-env/*.sh` script checks for these upfront and stops with clear
instructions if something is missing — none of them ever run an install
command on your behalf, and none of them touch `~/.kube/config` or any
other system/user kubeconfig (see below).

### `dev-env/` layout

```text
dev-env/validate.sh                     Combined orchestration: run/down/status the whole pipeline in one command
dev-env/cluster.sh                      Local K8s cluster lifecycle (kind + MetalLB): up/deploy-client/down/status
dev-env/vm.sh                           Local VM/jumphost lifecycle (emulated container): up/down/status
dev-env/targets.sh                      Fake "Remote cloud domain" destination containers: up/down/status
dev-env/run-vm-tests.sh                 Runs the VM-sourced tests against the emulated VM (docker cp/exec)
dev-env/suite.sh                        Syncs dev-env/reference-suite/ -> inputs/dev-local/ (sync subcommand)
dev-env/env.sh                          `source` this to point your shell's kubectl at the local cluster
dev-env/kind/                           kind cluster config + vendored MetalLB manifest + IPAddressPool template
dev-env/compose/                        docker-compose file for the fake Remote-cloud-domain targets
dev-env/reference-suite/                Git-tracked synthetic test plan (see below) -- synced into inputs/dev-local/
```

### Kubeconfig isolation

`kind`/`kubectl` calls made by `dev-env/cluster.sh` use an isolated
`KUBECONFIG` (`dev-env/.kubeconfig`, gitignored) — your real kubeconfig is
never read or written. To make your *own* shell's `kubectl` (and
`src/run_via_kubectl.sh`, which relies on the ambient kubectl context)
target the local cluster too, run:

```bash
source dev-env/env.sh
```

This only affects the current shell session; open a new one (or `unset
KUBECONFIG`) to go back to whatever you had configured before.

### Quick start

`dev-env/validate.sh` composes everything below into one command (see the
`validate-with-local-dev-env` skill for the full walkthrough):

```bash
dev-env/validate.sh run            # brings up cluster+targets+VM, syncs the suite, generates,
                                    # applies, runs both K8s- and VM-sourced tests, reports
cat outputs/dev-local/logs/summary-report.txt

# ...iterate on your change, re-run `dev-env/validate.sh run` as many times as needed...

dev-env/validate.sh down           # only once the development being validated is actually done
```

`--only k8s`/`--only vm` restrict this to just one path (e.g. `--only vm`
needs no K8s cluster at all when you're only iterating on `run_probe.py`).
`dev-env/validate.sh status` shows the combined state of everything.

Equivalent manual sequence, useful when debugging a single step (each
piece is independently documented in the `create-local-k8s-cluster` and
`create-local-vm` skills):

```bash
dev-env/cluster.sh up              # kind cluster + MetalLB
dev-env/cluster.sh deploy-client   # test client pod
dev-env/targets.sh up              # fake Remote-cloud-domain target containers
dev-env/vm.sh up                   # emulated VM/jumphost container
dev-env/suite.sh sync              # copies dev-env/reference-suite/ -> inputs/dev-local/ with real local IPs

source dev-env/env.sh
uv run src/generate_test_spec.py --suite dev-local
uv run src/generate_server_manifests.py --suite dev-local
kubectl apply -f outputs/dev-local/manifests/servers/local/ -n default

src/run_via_kubectl.sh --suite dev-local      # runs the K8s-Cluster-sourced cases
dev-env/run-vm-tests.sh --suite dev-local     # runs the VM-sourced cases (or the real standalone script)

uv run src/generate_report.py --suite dev-local
cat outputs/dev-local/logs/summary-report.txt

dev-env/vm.sh down
dev-env/targets.sh down
dev-env/cluster.sh down
```

Each `up` is idempotent; each `down` is the exact inverse. Add `-y`/`--yes`
to auto-confirm cleanup if provisioning fails partway through — by
default you're asked interactively (default "no"), so a partial failure
can be inspected/debugged instead of silently destroyed. Infra is meant to
stay up for the length of a development session (re-running `validate.sh
run` is cheap once it's up): bring it up when you start, tear it down
explicitly once you're done, not after every single run.

### The `dev-local` synthetic reference suite

`dev-env/reference-suite/` is a small, git-tracked test plan (same CSV
columns as a real suite, IPs replaced with placeholder tokens) designed to
exercise every automatable verdict plus the manual/skipped case, across
both source types (`K8s Cluster` and `VM`) and both protocols. See
`dev-env/reference-suite/NOTES.md` for exactly which row produces which
verdict and how the placeholder tokens map to addresses on your machine.
It's synced (never hand-edited in place) into `inputs/dev-local/` — edit
`dev-env/reference-suite/` and re-run `dev-env/suite.sh sync` instead.

### Known limitations

- **Shared `kind` docker network**: `kind` uses one docker network named
  `kind`, shared by every kind cluster on the machine, not just this
  project's. `dev-env/cluster.sh` discovers its actual subnet rather than
  assuming one, so this works even if you already use kind elsewhere — but
  the network itself is only removed once no kind cluster remains at all.
- **`--network host` on Docker Desktop/WSL2**: the "real" VM validation
  mode (running the generated standalone script directly, see the
  `create-local-vm` skill) depends on `--network host` sharing the actual
  host network stack, which has had inconsistent support under Docker
  Desktop's WSL2 integration. The "emulated" mode (`dev-env/vm.sh`) doesn't
  have this dependency.
- **Not a substitute for the real firewall validation** — see the warning
  at the top of this section.
- **`dev-env/validate.sh run --only vm` still creates the shared `kind`
  docker network** even though no K8s cluster is created — the fake
  Remote-cloud-domain targets need it for both modes. `dev-env/targets.sh`
  creates it itself if missing (see `ensure_kind_network()` in
  `dev-env/lib/common.sh`); a later `dev-env/cluster.sh up` reuses the same
  network rather than conflicting with it.

See the `validate-with-local-dev-env` skill for the combined orchestration
(`dev-env/validate.sh`), and `create-local-k8s-cluster`/`create-local-vm`
for detailed, step-by-step guidance on each half of this.

## Verdict interpretation

Each verdict printed by `run_probe.py` (in the log and in the terminal
summary) is prefixed with one of these icons: ✅ network/firewall confirmed
open (case A) or full pass, ⚠️ weaker/inconclusive signal (case B), ❌
inconclusive or blocked (case C / send error), ⏭️ skipped (manual test
required).

| Verdict | Meaning |
| --- | --- |
| ✅ `PASS` | TCP connection established — firewall and service OK |
| ✅ `PORT_REFUSED_NETWORK_OPEN` | Immediate TCP refusal (RST) — case A: network/firewall open up to the port, service missing in Remote cloud domain |
| ⚠️ `PORT_CLOSED_HOST_REACHABLE` | TCP times out but the host responds to ping — case B: service probably missing, weaker signal than an explicit refusal |
| ❌ `HOST_UNREACHABLE` | Neither the port nor ping respond — case C, inconclusive: check the firewall rule/route |
| ✅ `UDP_REFUSED_NETWORK_OPEN` | ICMP port-unreachable received after the UDP send — UDP equivalent of case A |
| ⚠️ `UDP_SENT_HOST_REACHABLE` | UDP datagram sent with no error and host responds to ping, but no ICMP observed — inconclusive, confirm with the receiving team |
| ⚠️ `UDP_SENT_HOST_UNREACHABLE` | UDP datagram sent with no socket error, and the host doesn't respond to ping either — still inconclusive (see the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools) appendix, UDP section): ping failure alone doesn't confirm a block |
| ❌ `UDP_SEND_FAILED` | Socket error while sending the UDP datagram |
| ⏭️ `SKIPPED_MANUAL_TEST_REQUIRED` | Local cloud domain acts as server: requires someone in Remote cloud domain to test it manually (section 2) |

See the ["Step-by-step manual diagnosis"](#step-by-step-manual-diagnosis-no-scripts)
appendix for the detail on how each verdict is reached and how
to reproduce it by hand.

The four ⚠️/❌ weak-or-inconclusive verdicts above (`PORT_CLOSED_HOST_REACHABLE`,
`HOST_UNREACHABLE`, `UDP_SENT_HOST_REACHABLE`, `UDP_SENT_HOST_UNREACHABLE`)
also carry a short diagnostic-nuance comment in `generate_report.py`'s
summary (see section 5) — the exact phrasing lives in `VERDICT_COMMENT` in
`src/run_probe.py`, not duplicated here. The report additionally shows a
`❔ NOT_RUN_YET` status that is **not** one of `run_probe.py`'s own
verdicts (it's absent from `VERDICT_ICON`) — it only means the report
found no logged result for that test id yet.

## Low-level: raw Linux tools

Useful when `run_probe.py` isn't available in the shell you have, or you
want to sanity-check the diagnosis independently, command by command. This
is exactly what `run_probe.py` automates — reproducing it by hand keeps the
tool from being a black box.

### TCP

```bash
nc -zv -w3 <ip> <port>
# Refused (case A, strong signal): the packet reached the host and the firewall let it through -- only the service is missing.
#   nc: connect to 127.0.0.1 port 54329 (tcp) failed: Connection refused
# Timeout (cases B/C, no RST): says nothing on its own, keep reading below.
#   nc: connect to 203.0.113.1 port 12345 (tcp) timed out: Operation now in progress

# Alternative without nc (same "Connection refused" signal, via bash):
bash -c 'cat < /dev/tcp/<ip>/<port>'
```

The message **"Connection refused" is always case A** (immediate refusal,
open network): no further test is needed, only the service is missing. Any
other outcome (timeout, "No route to host", no response) is ambiguous by
itself, so run the complementary tests:

```bash
ping -c4 <ip>              # proves the host itself is reachable, independent of the TCP port
tcptraceroute <ip> <port>  # traces the path AT that TCP port specifically, to see where it's cut off
```

If `ping` responds, it's case B (service probably not deployed, but a
firewall that lets ICMP through while blocking that one TCP port can't be
ruled out). If `ping` doesn't respond either, it's case C (inconclusive —
suspect the firewall rule or the route). See the decision table in the
["Step-by-step manual diagnosis"](#step-by-step-manual-diagnosis-no-scripts)
appendix, below.

For protocols with a TLS/application layer on top, `curl -kv https://<ip>:<port>`
or `openssl s_client -connect <ip>:<port>` go one step further than `nc`:
they complete the TCP handshake *and* attempt the TLS handshake/HTTP
request, useful to tell "port open but cert/app rejects it" apart from a
plain network problem.

### UDP

Unlike TCP, UDP has no handshake, so there's no direct "Connection refused"
to look for with `nc`. The destination *can* reply with an ICMP
*port-unreachable* when nothing is listening, but unlike TCP's RST, **many
corporate firewalls filter that return ICMP** even though the UDP datagram
itself gets through fine — so its absence proves nothing (it's the common
case even with everything correctly configured); its presence, if observed,
is as strong a signal as case A in TCP.

`nc`/`bash` don't expose that ICMP reliably. Reproduce it by hand with this
Python one-liner (the same trick `run_probe.py` uses: a "connected" UDP
socket + double send, since on Linux the pending ICMP is delivered on the
next socket operation, not on the first `send`):

```bash
python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.connect(('<ip>', <port>)); s.send(b'probe')
time.sleep(0.5)
try:
    s.send(b'probe'); print('no ICMP port-unreachable (inconclusive)')
except ConnectionRefusedError:
    print('ICMP port-unreachable received -> port refused, network open')
"
```

If it prints "ICMP port-unreachable received", diagnosis done (case A). If
not, the only thing left to check is `ping -c4 <ip>` as a weak proxy for
host reachability (it does **not** confirm whether the UDP packet itself got
through — ICMP echo is often filtered independently of the data port) and,
ultimately, confirm with the receiving team in Remote cloud domain whether
the packet arrived.

See the ["Step-by-step manual diagnosis"](#step-by-step-manual-diagnosis-no-scripts)
appendix, below, for the full decision table that combines these signals into
a verdict.

## Step-by-step manual diagnosis (no scripts)

Everything `src/run_probe.py` does can be reproduced by hand with
common Linux tools (the ["Low-level: raw Linux tools"](#low-level-raw-linux-tools)
appendix, above, has the exact commands and the rationale
behind each one, both for TCP and UDP) — this is intentional: the
automation should not be a black box. This section explains how to combine
what you observe into a diagnosis, distinguishing three situations:

- **A) The server on the other side doesn't exist, but the network is open**
  (strong signal): the TCP connection is **refused instantly**
  (`RST`, "Connection refused"). The packet reached the destination host and the
  firewall let it through — only the service is missing.
- **B) The server probably doesn't exist, but with less certainty than in A**:
  the TCP connection **times out** (total silence, no `RST`) but
  `ping`/`tcptraceroute` do reach the host. Consistent with "nothing listening
  on that port", but also with a firewall that lets ICMP through and selectively
  filters that TCP port — weaker signal than A.
- **C) Neither of the above** (inconclusive / suspect the
  firewall): neither the port nor `ping`/`tcptraceroute` respond. From the outside
  you can't distinguish "firewall rule not applied" from "host powered off"; the
  first reasonable suspicion is the firewall rule.

### Decision table

| Observed signal | Conclusion | Equivalent verdict in `run_probe.py` |
| --- | --- | --- |
| TCP connection established | Connectivity OK | `PASS` |
| TCP refused instantly ("Connection refused") | **A**: network open up to the host, service missing | `PORT_REFUSED_NETWORK_OPEN` |
| TCP times out, but `ping`/`tcptraceroute` reach the host | **B**: service probably missing, weaker signal than A | `PORT_CLOSED_HOST_REACHABLE` |
| TCP times out and `ping`/`tcptraceroute` don't reach either | **C**: inconclusive / suspect the firewall | `HOST_UNREACHABLE` |
| UDP: ICMP port-unreachable received after sending | **A** (UDP equivalent): network open, listener missing | `UDP_REFUSED_NETWORK_OPEN` |
| UDP: sent with no observable error, `ping` OK | See the [raw-tools appendix](#low-level-raw-linux-tools), UDP — inconclusive | `UDP_SENT_HOST_REACHABLE` |
| UDP: sent with no observable error, `ping` fails | See the [raw-tools appendix](#low-level-raw-linux-tools), UDP — inconclusive | `UDP_SENT_HOST_UNREACHABLE` |

### Full manual procedure (example: Netcool UDP 1167)

1. Deploy/enter the client in Local cloud domain (section 4.1), and pick
   between high-level (section 4.2) or low-level ([raw-tools appendix](#low-level-raw-linux-tools), above) tools.
2. Attempt the connection with the tool for the corresponding protocol:
   - TCP: `nc -zv -w3 10.180.141.111 443` ([raw-tools appendix](#low-level-raw-linux-tools)) or
     `run_probe.py tcp 10.180.141.111 443` (section 4.2).
   - UDP: the Python one-liner from the [raw-tools appendix](#low-level-raw-linux-tools), or `run_probe.py udp 10.45.66.48 1167` (section 4.2).
3. If the result is "Connection refused" (TCP) or ICMP port-unreachable
   (UDP) → **case A**, diagnosis done: the service is missing, it's not the firewall.
4. If not, `ping -c4 <ip>`.
5. If the ping responds, `tcptraceroute <ip> <port>` (TCP) or `traceroute
   <ip>` (UDP) to confirm the path reaches the destination.
6. Apply the decision table above with what you observed in 2-5. (The
   section-4.2 subcommands do steps 2-5 for you and print the resulting verdict directly.)
