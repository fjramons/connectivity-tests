---
name: run-local-cloud-domain-connectivity-tests
description: Guide for running the automated Local cloud domain -> Remote cloud domain connectivity tests, choosing the right backend (K8s from the lab PC, or VM/jumphost with the self-contained script) and consolidating results in outputs/.
---
# Run the Local cloud domain → Remote cloud domain connectivity tests

Only the `local_cloud_domain_to_remote_cloud_domain` direction is automated
(Local cloud domain acts as client): it's the only one that can be launched
without depending on someone in Remote cloud domain doing something. The
`remote_cloud_domain_to_local_cloud_domain` cases require manual coordination
with Remote cloud domain — see section 3 of `README.md`.

## Suites

Every test suite (e.g. a firewall matrix version) lives in its own named
subfolder: `inputs/<suite>/connectivity-test-spec.json`,
`outputs/<suite>/manifests/servers/`, `outputs/<suite>/standalone/`,
`outputs/<suite>/logs/`, and optionally `inputs/<suite>/connectivity-tests.toml`
if that suite needs a config override (most don't — they use the generic
`connectivity-tests.toml` at the repo root). Commands below use a suite,
via `--suite <name>` (or the equivalent flag on `run_via_kubectl.sh`) or
by exporting `TEST_SUITE=<name>` once per shell session; if neither is
given, the `default` suite (`inputs/default/`) is used automatically, with
a printed notice. `ls inputs/` lists the suites that currently exist on
disk.

## Choosing the backend based on `source.type`

Each test case in `inputs/<suite>/connectivity-test-spec.json` has a
`source.type`: `"K8s Cluster"` or `"VM"`. The backend to use depends on that:

- **`source.type == "K8s Cluster"`** → K8s backend, run **from the lab
  PC** (has `kubectl` with direct access to the cluster):
  ```bash
  kubectl apply -f manifests/netshoot-client-k8s.yaml   # once, if it doesn't already exist
  src/run_via_kubectl.sh --suite <name> [--namespace <ns>] [--deployment <name>]
  ```
  The log ends up automatically at
  `outputs/<suite>/logs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.log`.

- **`source.type == "VM"`** → VM/jumphost backend. On the dev PC, generate
  the self-contained script (if it isn't already generated or the spec changed):
  ```bash
  uv run src/generate_standalone_script.py --suite <name>
  ```
  Take `outputs/<name>/standalone/local-cloud-domain-to-remote-cloud-domain-vm-tests.sh` to the lab PC (OneDrive
  Web), open a session to the jumphost/VM from there, and **paste the file's
  entire content** into the terminal (no file transfer needed: the script
  writes its own temp files locally and only needs Docker). Copy the
  log block delimited by `===== LOG START =====` / `===== LOG END =====`
  into a new file in `outputs/<name>/logs/` on the lab PC. It then drops you into an
  interactive shell with `run_probe.py` + the spec already at `/data` —
  see "Ad hoc single-case tests" below.

Both backends run `run_probe.py`'s `batch` subcommand internally (the
original full-battery mode); its CLI also has `tcp`/`udp`/`list`
subcommands for single-case use, see below.

## Ad hoc single-case tests

To spot-check a single destination without running the whole battery,
`run_probe.py` (already copied to the pod/VM by either backend above) can
be invoked directly, following the exact same TCP/UDP diagnosis
methodology as `batch` but printing the full detail to the terminal:

```bash
python3 run_probe.py list --spec spec.json          # id, destination IP, port, protocol of known cases
python3 run_probe.py tcp <ip> <port>
python3 run_probe.py udp <ip> <port> --config connectivity-tests.toml   # --config optional
```

For K8s, these run inside the pod's persistent `/tmp` (left there by
`run_via_kubectl.sh`); for VM/jumphost, inside the interactive shell the
standalone script drops you into at the end. See README.md section 2.2 for
the full explanation and section 2.3 for the low-level (raw Linux tools)
equivalent.

## Interpreting the results

Before concluding the firewall is misconfigured, check the full verdict — a
TCP/UDP `FAIL` alone doesn't confirm a firewall problem, because the
Remote cloud domain service might not be deployed yet:

`run_probe.py` prefixes each verdict (in the log and the terminal summary)
with an icon: ✅ network/firewall confirmed open, ⚠️ weaker/inconclusive
signal, ❌ inconclusive or blocked, ⏭️ skipped.

- ✅ `PASS`: connectivity OK.
- ✅ `PORT_REFUSED_NETWORK_OPEN`: immediate TCP refusal (RST) — case A, the
  strongest signal: the network/firewall let traffic through to that port, only
  the service is missing from Remote cloud domain. Not a firewall
  problem.
- ⚠️ `PORT_CLOSED_HOST_REACHABLE`: TCP times out (no RST) but the host
  responds to ping — case B, weaker signal than an explicit refusal:
  the service is probably not deployed yet, but it could also be
  a firewall that lets ICMP through and selectively filters that port.
- ❌ `HOST_UNREACHABLE`: neither the port nor ping respond — case C,
  inconclusive: check the firewall rule or the route.
- ✅ `UDP_REFUSED_NETWORK_OPEN`: an ICMP port-unreachable was received after
  sending — UDP equivalent of case A.
- ⚠️ `UDP_SENT_*`: no ICMP observed, UDP doesn't confirm delivery — unlike
  TCP, the absence of a refusal is NOT conclusive (many firewalls filter that
  return ICMP, and ICMP echo/ping is often filtered independently of the
  data port too); the ping result is only extra context (whether the host
  answered ping or not), not a good/bad signal by itself — both `UDP_SENT_*`
  verdicts are equally inconclusive, confirm with the receiving team if needed.
- ⏭️ `SKIPPED_MANUAL_TEST_REQUIRED`: `remote_cloud_domain_to_local_cloud_domain` case, not attempted
  automatically — see section 3 of `README.md`.

If several cases in a row give `HOST_UNREACHABLE` for the same destination, suspect
a firewall rule that wasn't applied correctly rather than a one-off service
problem. Section 5 of `README.md` documents how to reproduce by hand
(without scripts) the same A/B/C diagnosis this runner applies.
