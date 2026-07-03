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
consistently everywhere (`inputs/<name>/`, `outputs/<name>/manifests/servers/`,
`outputs/<name>/standalone/`, `outputs/<name>/logs/`, and optionally
`inputs/<name>/connectivity-tests.toml` if that suite needs its own config
override). A suite is always required, one way or the other. `ls inputs/`
lists the suites that currently exist.

## The three environments

This project moves across three environments with very different capabilities:

| Environment | Network access | What happens there |
| --- | --- | --- |
| **Dev PC** | No access to Local cloud domain | Generate the spec, the K8s manifests, and the self-contained script, with `uv` |
| **Lab PC** | Direct `kubectl` to the Local cloud domain clusters, and access to the jumphost (SSH) | Apply K8s manifests, run the automation against clusters, open a session to the jumphost |
| **Jumphost / VM in Local cloud domain** | Direct access to Remote cloud domain, but transferring files is hard | Paste the self-contained script generated on the dev PC, or use Docker Compose manually |

Artifacts generated on the dev PC (`inputs/<suite>/connectivity-test-spec.*`,
`outputs/<suite>/manifests/servers/`, `outputs/<suite>/standalone/`) are moved to the lab PC manually via
**OneDrive Web** (not automatable). From there:

- Everything related to **K8s** (`kubectl apply` / `exec` / `cp`) runs
  directly from the lab PC.
- Everything related to **VM/Docker** runs by opening a session to the
  jumphost and pasting the `standalone/` script there (self-contained: no
  `scp` required).

## Prerequisites and installation (dev PC)

- [`uv`](https://docs.astral.sh/uv/) installed:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- Python managed by `uv` (no need to install it separately):

  ```bash
  uv python install 3.12
  ```

- Create the virtual environment with the dependencies (`pyyaml`) declared
  in `pyproject.toml`:

  ```bash
  uv sync
  ```

- Docker (optional, only if you want to test the `nicolaka/netshoot:v0.15`
  image locally before taking it to Local cloud domain).

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
outputs/<suite>/manifests/servers/        SERVER (per destination) K8s manifests, one subtree per suite
outputs/<suite>/standalone/               Self-contained script(s) to paste into the jumphost/VM
outputs/<suite>/logs/                     Run logs
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
section 3) — it is not embedded into the generated YAML.

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

## 2. Manual tests as a client in Local cloud domain

### 2.1 Deploy the client

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

Both give you a shell with the tools (`nicolaka/netshoot:v0.15`) used in 2.2
and 2.3 below.

### 2.2 High-level: `run_probe.py` subcommands (recommended)

`run_probe.py` — the same engine that drives the automated battery (section
4) — can also be invoked for a single case at a time, following the exact
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
  (see the UDP note in 2.3).

- `--help` works at every level: `run_probe.py --help`, `run_probe.py tcp --help`, etc.

Where to find `run_probe.py` + `spec.json` already in place, without extra
file transfers:

- **K8s**: after running `src/run_via_kubectl.sh` at least once (section 4),
  both files remain in `/tmp` inside the pod (a persistent Deployment, not
  an ephemeral job) — `kubectl exec -it deploy/netshoot-client -n <namespace> -- bash`
  and run the commands above against `/tmp/run_probe.py --spec /tmp/spec.json`.
  If you haven't run the battery yet, `kubectl cp` them in yourself the same
  way the script does.
- **VM/jumphost**: pasting the self-contained script (section 4) already
  ends by dropping you into an interactive shell with both files at
  `/data` — just run the commands above there.

If any of these commands fails, don't stop at "it doesn't work": section 5
explains how to read the failure to know whether it's a firewall problem or
simply that the Remote cloud domain service isn't deployed yet.

### 2.3 Low-level: raw Linux tools

Useful when `run_probe.py` isn't available in the shell you have, or you
want to sanity-check the diagnosis independently, command by command. This
is exactly what `run_probe.py` automates — reproducing it by hand keeps the
tool from being a black box.

#### TCP

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
suspect the firewall rule or the route). See the decision table in
section 5.

For protocols with a TLS/application layer on top, `curl -kv https://<ip>:<port>`
or `openssl s_client -connect <ip>:<port>` go one step further than `nc`:
they complete the TCP handshake *and* attempt the TLS handshake/HTTP
request, useful to tell "port open but cert/app rejects it" apart from a
plain network problem.

#### UDP

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

See section 5 for the full decision table that combines these signals into
a verdict.

## 3. Test servers in Local cloud domain and how to test them from Remote cloud domain

The `remote_cloud_domain_to_local_cloud_domain` destinations (Spotfire, Vertica/Olap DB, IAM/Keycloak,
CMM Ingress) already belong to real apps that aren't deployed yet. To
be able to validate the firewall without waiting for those apps to be ready, generate a
test server manifest **for each unique destination** (same IP:port as
the real app):

```bash
uv run src/generate_server_manifests.py --suite cne2.0-v0.22
kubectl apply -f outputs/cne2.0-v0.22/manifests/servers/<slug>-k8s.yaml -n <namespace>
```

`manifests/netshoot-client-k8s.yaml` and
`manifests/netshoot-client-docker-compose.yml` (section 2.1) are **not**
suite-specific: they're generic client tooling with no embedded spec data,
and always stay at the `manifests/` root regardless of which suite you're
testing.

`<namespace>` comes from the `namespace` key in the resolved config file
(see "Config resolution" in section 1; `"default"` unless set) — it is not
baked into the manifest, deploy explicitly with `-n` for clarity; the
generator also prints this same command with the configured namespace
filled in, and each manifest's header comment repeats it.

Each manifest deploys the same `nicolaka/netshoot:v0.15` container acting
as a listener (`socat`) on the exact port of the real app, with its own `Service
type: LoadBalancer` that requests the real app's IP explicitly (`spec.loadBalancerIP`
plus the `metallb.io/loadBalancerIPs` annotation, for compatibility with both
older and current MetalLB). **Do not deploy it together with the real app**
on the same port. If a real `Service` already exists with that LoadBalancer IP
already reserved and authorized in the firewall, edit that manifest's `Service`
(or the real one's selector) to avoid a conflict — the generated YAML
itself includes this warning as a comment.

Once deployed, ask someone in Remote cloud domain to test with
common Linux tools (see section 2.3 for the rationale behind each one — same
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
the `Service` is using a different LoadBalancer IP than the authorized one. Section 5
explains, step by step, how to distinguish both cases with the same
tools (`nc`, `ping`, `traceroute`) used above.

## 4. Automation Local cloud domain → Remote cloud domain

Only this direction is automated (Local cloud domain acts as client), because
it's the one we can run without depending on someone in Remote cloud domain doing
something. The test engine (`src/run_probe.py`) only uses the Python
standard library: it attempts a TCP connection or a UDP send, and if it fails (or always, for
UDP, which doesn't confirm delivery) it runs `ping`/`tcptraceroute` as complementary
diagnostics to distinguish "host unreachable" from "port/service down but
host alive". `remote_cloud_domain_to_local_cloud_domain` cases are logged as
`SKIPPED_MANUAL_TEST_REQUIRED`
(see section 3). Besides the verdicts, the log includes additional signals
that help interpret a failure without having to repeat it by hand: if a TCP
failure came with an explicit "no route to host"/"network unreachable" instead of a
silent timeout, it's noted as such; and after a `traceroute`/`tcptraceroute`
a summary sentence is added indicating how far the traffic reached and whether
it reached the destination itself or not (see section 5 for the detail on how this is interpreted).

The wait margin used in the UDP ICMP detection trick (see
section 2.3, UDP) is computed from the RTT measured by `ping` to that
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
`kubectl exec ... run_probe.py batch ...`, and copies the resulting log to
`outputs/<suite>/logs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.log`.
All from the lab PC, without going through the jumphost. Those copied files
stay in the pod's `/tmp` afterward, ready for the ad hoc `list`/`tcp`/`udp`
subcommands from section 2.2.

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
`===== LOG START =====` / `===== LOG END =====`: copy that block and
save it as `outputs/cne2.0-v0.22/logs/local-cloud-domain-to-remote-cloud-domain-vm-tests-<timestamp>.log`
on the lab PC. It then drops you into an interactive shell with
`run_probe.py` and the spec still present at `/data`, for the ad hoc
`list`/`tcp`/`udp` subcommands from section 2.2 — `exit` when done to clean
up the temp files.

## 5. Step-by-step manual diagnosis (no scripts)

Everything `src/run_probe.py` does can be reproduced by hand with
common Linux tools (section 2.3 has the exact commands and the rationale
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
| UDP: sent with no observable error, `ping` OK | See section 2.3, UDP — inconclusive | `UDP_SENT_HOST_REACHABLE` |
| UDP: sent with no observable error, `ping` fails | See section 2.3, UDP — inconclusive | `UDP_SENT_HOST_UNREACHABLE` |

### Full manual procedure (example: Netcool UDP 1167)

1. Deploy/enter the client in Local cloud domain (section 2.1), and pick
   between high-level (2.2) or low-level (2.3) tools.
2. Attempt the connection with the tool for the corresponding protocol:
   - TCP: `nc -zv -w3 10.180.141.111 443` (2.3) or
     `run_probe.py tcp 10.180.141.111 443` (2.2).
   - UDP: the Python one-liner from 2.3, or `run_probe.py udp 10.45.66.48 1167` (2.2).
3. If the result is "Connection refused" (TCP) or ICMP port-unreachable
   (UDP) → **case A**, diagnosis done: the service is missing, it's not the firewall.
4. If not, `ping -c4 <ip>`.
5. If the ping responds, `tcptraceroute <ip> <port>` (TCP) or `traceroute
   <ip>` (UDP) to confirm the path reaches the destination.
6. Apply the decision table above with what you observed in 2-5. (The 2.2
   subcommands do steps 2-5 for you and print the resulting verdict directly.)

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
| ⚠️ `UDP_SENT_HOST_UNREACHABLE` | UDP datagram sent with no socket error, and the host doesn't respond to ping either — still inconclusive (see section 2.3, UDP): ping failure alone doesn't confirm a block |
| ❌ `UDP_SEND_FAILED` | Socket error while sending the UDP datagram |
| ⏭️ `SKIPPED_MANUAL_TEST_REQUIRED` | Local cloud domain acts as server: requires someone in Remote cloud domain to test it manually (section 3) |

See section 5 for the detail on how each verdict is reached and how
to reproduce it by hand.
