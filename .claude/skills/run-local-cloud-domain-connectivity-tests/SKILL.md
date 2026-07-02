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

## Choosing the backend based on `source.type`

Each test case in `inputs/connectivity-test-spec.json` has a
`source.type`: `"K8s Cluster"` or `"VM"`. The backend to use depends on that:

- **`source.type == "K8s Cluster"`** → K8s backend, run **from the lab
  PC** (has `kubectl` with direct access to the cluster):
  ```bash
  kubectl apply -f manifests/netshoot-client-k8s.yaml   # once, if it doesn't already exist
  src/run_via_kubectl.sh [namespace] [deployment-name]
  ```
  The log ends up automatically at
  `outputs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.log`.

- **`source.type == "VM"`** → VM/jumphost backend. On the dev PC, generate
  the self-contained script (if it isn't already generated or the spec changed):
  ```bash
  uv run src/generate_standalone_script.py
  ```
  Take `standalone/local-cloud-domain-to-remote-cloud-domain-vm-tests.sh` to the lab PC (OneDrive
  Web), open a session to the jumphost/VM from there, and **paste the file's
  entire content** into the terminal (no file transfer needed: the script
  writes its own temp files locally and only needs Docker). Copy the
  log block delimited by `===== LOG START =====` / `===== LOG END =====`
  into a new file in `outputs/` on the lab PC.

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
- ⚠️/❌ `UDP_SENT_*`: no ICMP observed, UDP doesn't confirm delivery — unlike
  TCP, the absence of a refusal is NOT conclusive (many firewalls filter that
  return ICMP); use the ping result as a reachability proxy (⚠️ if it
  responds, ❌ if it doesn't), and confirm with the receiving team if needed.
- ⏭️ `SKIPPED_MANUAL_TEST_REQUIRED`: `remote_cloud_domain_to_local_cloud_domain` case, not attempted
  automatically — see section 3 of `README.md`.

If several cases in a row give `HOST_UNREACHABLE` for the same destination, suspect
a firewall rule that wasn't applied correctly rather than a one-off service
problem. Section 5 of `README.md` documents how to reproduce by hand
(without scripts) the same A/B/C diagnosis this runner applies.
