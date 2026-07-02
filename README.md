# Connectivity tests Local cloud domain ⟷ Remote cloud domain

Tools to systematically validate connectivity and the firewall rules
open between "Remote cloud domain" and "Local cloud domain",
based on the rule matrix in `inputs/*.csv`.

## The three environments

This project moves across three environments with very different capabilities:

| Environment | Network access | What happens there |
| --- | --- | --- |
| **Dev PC** | No access to Local cloud domain | Generate the spec, the K8s manifests, and the self-contained script, with `uv` |
| **Lab PC** | Direct `kubectl` to the Local cloud domain clusters, and access to the jumphost (SSH) | Apply K8s manifests, run the automation against clusters, open a session to the jumphost |
| **Jumphost / VM in Local cloud domain** | Direct access to Remote cloud domain, but transferring files is hard | Paste the self-contained script generated on the dev PC, or use Docker Compose manually |

Artifacts generated on the dev PC (`inputs/connectivity-test-spec.*`,
`manifests/`, `standalone/`) are moved to the lab PC manually via
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
inputs/                Source CSVs + generated spec (readable YAML + JSON for the runner)
connectivity-tests.toml  Generator config (port<->protocol pairing)
src/                    Scripts (generators on the dev PC, stdlib-only runner)
manifests/              CLIENT (netshoot) and SERVER (per destination) K8s/Compose manifests
standalone/             Self-contained script(s) to paste into the jumphost/VM
outputs/                Run logs
```

## 1. Generate the test specification

From the two CSVs in `inputs/`:

```bash
uv run src/generate_test_spec.py
```

This generates `inputs/connectivity-test-spec.yaml` (readable, hand-editable) and its
twin `inputs/connectivity-test-spec.json` (the one the runner actually reads,
with no dependency on PyYAML inside Local cloud domain).

Each CSV expands into individual test cases (one IP × one port), including
lists (`10.2.113.129, 10.2.113.131`) and ranges (`10.180.141.99-10.180.141.105`).
When ports and protocols have the same number of elements in a row
(e.g. 3 ports and 3 protocols), the pairing is controlled from
`connectivity-tests.toml` (`port_protocol_pairing`: `one_to_one` by default,
or `cross_product`); it can also be forced for a single run with
`--port-protocol-pairing cross_product`.

If you edit the YAML by hand (for example, to annotate or fix a case in
`unresolved`), resync only the JSON without re-reading the CSVs:

```bash
uv run src/generate_test_spec.py --from-yaml
```

Each test case indicates `direction` (`local_cloud_domain_to_remote_cloud_domain`
if Local cloud domain acts as client, `remote_cloud_domain_to_local_cloud_domain`
if it acts as server) and `automatable` (only `true` for
`local_cloud_domain_to_remote_cloud_domain`, which is the only thing we can run without
depending on someone in Remote cloud domain doing something).

## 2. Launch test clients in Local cloud domain (manual)

### On a K8s cluster

```bash
kubectl apply -f manifests/netshoot-client-k8s.yaml   # deploy in the same namespace as the real app
kubectl exec -it deploy/netshoot-client -- bash
```

Inside the pod, with the tools already included in `nicolaka/netshoot:v0.15`:

```bash
nc -zv 10.45.66.48 1167          # TCP: is the port open?
nc -u -zv 10.45.66.48 1167       # UDP: best-effort send
curl -kv https://10.180.141.111:443
openssl s_client -connect 10.180.141.110:6566
ping -c4 10.45.66.48
tcptraceroute 10.45.66.48 1167   # port-level TCP traceroute
```

### On a VM (Docker Compose)

```bash
docker compose -f manifests/netshoot-client-docker-compose.yml up -d
docker compose -f manifests/netshoot-client-docker-compose.yml exec netshoot bash
```

(same commands as above; `network_mode: host` makes traffic leave with
the VM's own IP).

If any of these commands fails, don't stop at "it doesn't work": section 5
explains how to read the failure to know whether it's a firewall problem or
simply that the Remote cloud domain service isn't deployed yet.

## 3. Test servers in Local cloud domain and how to test them from Remote cloud domain

The `remote_cloud_domain_to_local_cloud_domain` destinations (Spotfire, Vertica/Olap DB, IAM/Keycloak,
CMM Ingress) already belong to real apps that aren't deployed yet. To
be able to validate the firewall without waiting for those apps to be ready, generate a
test server manifest **for each unique destination** (same IP:port as
the real app):

```bash
uv run src/generate_server_manifests.py
kubectl apply -f manifests/servers/<slug>-k8s.yaml
```

Each manifest deploys the same `nicolaka/netshoot:v0.15` container acting
as a listener (`socat`) on the exact port of the real app, with its own `Service
type: LoadBalancer`. **Do not deploy it together with the real app** on the same
port. If a real `Service` already exists with that LoadBalancer IP already reserved and
authorized in the firewall, edit that manifest's `Service` (or the real one's
selector) to avoid creating a new, unauthorized IP — the generated YAML
itself includes this warning as a comment.

Once deployed, ask someone in Remote cloud domain to test with
common Linux tools:

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
section 5, UDP note) is computed from the RTT measured by `ping` to that
same host, and is configurable in `connectivity-tests.toml` (`[probe]` table)
if the default values don't fit the real Local cloud domain → Remote cloud domain latency;
both backends below use it automatically if the file exists.

### K8s backend (from the lab PC)

```bash
kubectl apply -f manifests/netshoot-client-k8s.yaml   # once
src/run_via_kubectl.sh [namespace] [deployment-name]
```

The script does a `kubectl cp` of `run_probe.py`, the spec, and
`connectivity-tests.toml` (if it exists) to the pod, runs it with `kubectl exec`, and
copies the resulting log to `outputs/local-cloud-domain-to-remote-cloud-domain-k8s-<timestamp>.log`.
All from the lab PC, without going through the jumphost.

### VM/jumphost backend (self-contained script)

On the dev PC, generate the script (it embeds the `run_probe.py` code,
the spec subset with `source.type == "VM"`, and `connectivity-tests.toml`
if it exists):

```bash
uv run src/generate_standalone_script.py
```

This creates `standalone/local-cloud-domain-to-remote-cloud-domain-vm-tests.sh`. Take it
to the lab PC (OneDrive Web), open an SSH session to the jumphost/VM from there, and
**paste the file's entire content** into the terminal (no `scp` needed: the
script writes its own temp files locally and only needs
Docker installed). When it finishes, it prints the log delimited by
`===== LOG START =====` / `===== LOG END =====`: copy that block and
save it as `outputs/local-cloud-domain-to-remote-cloud-domain-vm-tests-<timestamp>.log`
on the lab PC.

## 5. Step-by-step manual diagnosis (no scripts)

Everything `src/run_probe.py` does can be reproduced by hand with
common Linux tools — this is intentional: the automation should not
be a black box. This section explains how to interpret a connectivity
failure to distinguish three situations:

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

### How to distinguish "refused" from "timeout" by hand (TCP)

```bash
nc -zv -w3 <ip> <port>
# Real example of a refusal (case A):
#   nc: connect to 127.0.0.1 port 54329 (tcp) failed: Connection refused
# Real example of a timeout (cases B/C, no RST):
#   nc: connect to 203.0.113.1 port 12345 (tcp) timed out: Operation now in progress

# Alternative without nc (same error message, via bash):
bash -c 'cat < /dev/tcp/<ip>/<port>'
#   bash: connect: Connection refused        <- case A
#   (hangs until the bash/TCP timeout, no message) <- cases B/C
```

The message **"Connection refused" is always case A** (immediate refusal,
open network). Any other outcome (timeout, "No route to host", no
response) requires the next step to distinguish B from C.

### Decision table

| Observed signal | Conclusion | Equivalent verdict in `run_probe.py` |
| --- | --- | --- |
| TCP connection established | Connectivity OK | `PASS` |
| TCP refused instantly ("Connection refused") | **A**: network open up to the host, service missing | `PORT_REFUSED_NETWORK_OPEN` |
| TCP times out, but `ping`/`tcptraceroute` reach the host | **B**: service probably missing, weaker signal than A | `PORT_CLOSED_HOST_REACHABLE` |
| TCP times out and `ping`/`tcptraceroute` don't reach either | **C**: inconclusive / suspect the firewall | `HOST_UNREACHABLE` |
| UDP: ICMP port-unreachable received after sending | **A** (UDP equivalent): network open, listener missing | `UDP_REFUSED_NETWORK_OPEN` |
| UDP: sent with no observable error, `ping` OK | See UDP note — inconclusive | `UDP_SENT_HOST_REACHABLE` |
| UDP: sent with no observable error, `ping` fails | See UDP note — inconclusive | `UDP_SENT_HOST_UNREACHABLE` |

### Note on UDP

Unlike TCP, UDP has no handshake: there's no direct "Connection refused".
An equivalent exists — the destination host can respond with an
ICMP *port-unreachable* when nothing is listening on that port — but, unlike
TCP's `RST`, **many corporate firewalls filter that return
ICMP** even though the UDP itself gets through fine. Because of this:

- If the ICMP port-unreachable is observed, it's as strong a signal as
  case A in TCP (`UDP_REFUSED_NETWORK_OPEN`).
- If it's **not** observed, **it means nothing** (unlike a timeout in
  TCP): it's simply the most common case, even with the firewall correctly
  configured. The only option left is to use `ping` as a proxy for host reachability,
  and ultimately confirm with the receiving team in Remote cloud domain whether the
  packet arrived (e.g. checking Netcool logs/traces for the
  SNMP case).

`nc`/`bash` don't expose that ICMP reliably. To reproduce it by hand, use
this Python one-liner (the same trick used by `run_probe.py`: "connected" UDP
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

### Full manual procedure (example: Netcool UDP 1167)

1. Deploy/enter the client in Local cloud domain (section 2): `kubectl exec -it
   deploy/netshoot-client -- bash` or `docker compose ... exec netshoot bash`.
2. Attempt the connection with the tool for the corresponding protocol:
   - TCP: `nc -zv -w3 10.180.141.111 443`
   - UDP: the Python one-liner above, with the IP/port for the case (e.g.
     `10.45.66.48` / `1167`).
3. If the result is "Connection refused" (TCP) or ICMP port-unreachable
   (UDP) → **case A**, diagnosis done: the service is missing, it's not the firewall.
4. If not, `ping -c4 <ip>`.
5. If the ping responds, `tcptraceroute <ip> <port>` (TCP) or `traceroute
   <ip>` (UDP) to confirm the path reaches the destination.
6. Apply the decision table above with what you observed in 2-5.

## Verdict interpretation

| Verdict | Meaning |
| --- | --- |
| `PASS` | TCP connection established — firewall and service OK |
| `PORT_REFUSED_NETWORK_OPEN` | Immediate TCP refusal (RST) — case A: network/firewall open up to the port, service missing in Remote cloud domain |
| `PORT_CLOSED_HOST_REACHABLE` | TCP times out but the host responds to ping — case B: service probably missing, weaker signal than an explicit refusal |
| `HOST_UNREACHABLE` | Neither the port nor ping respond — case C, inconclusive: check the firewall rule/route |
| `UDP_REFUSED_NETWORK_OPEN` | ICMP port-unreachable received after the UDP send — UDP equivalent of case A |
| `UDP_SENT_HOST_REACHABLE` | UDP datagram sent with no error and host responds to ping, but no ICMP observed — inconclusive, confirm with the receiving team |
| `UDP_SENT_HOST_UNREACHABLE` | UDP datagram sent with no socket error, but the host doesn't respond to ping — possible firewall block |
| `UDP_SEND_FAILED` | Socket error while sending the UDP datagram |
| `SKIPPED_MANUAL_TEST_REQUIRED` | Local cloud domain acts as server: requires someone in Remote cloud domain to test it manually (section 3) |

See section 5 for the detail on how each verdict is reached and how
to reproduce it by hand.
