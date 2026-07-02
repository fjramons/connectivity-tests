# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Tooling to validate connectivity and firewall rules between "Domain 2" and
"Domain 3" (EC.3), driven by a firewall rule matrix delivered as two CSV
files in `inputs/`. It generates a normalized test spec, K8s/Compose
manifests, and scripts to actually run the connectivity checks — both
automated and fully-manual fallbacks (see README.md sections 2, 3 and 5 for
the manual procedures and how connectivity failures are diagnosed).

No test framework is configured; there is no `pytest`/CI. Validation of
changes is done by running the scripts directly (see "Verifying changes"
below).

## Setup and commands

```bash
uv sync                                  # create .venv with pyyaml (only dep)
uv run src/generate_test_spec.py         # CSV -> inputs/connectivity-test-spec.{yaml,json}
uv run src/generate_server_manifests.py  # spec -> manifests/servers/<slug>-k8s.yaml (one per unique destination)
uv run src/generate_standalone_script.py # spec + run_probe.py -> standalone/*.sh (self-contained, for the jumphost)
uv run python3 -m py_compile src/*.py    # syntax-check all Python scripts
bash -n <script>.sh                      # syntax-check generated/hand-written shell scripts
```

`src/run_probe.py` is invoked with plain `python3` (not `uv run`) because it
must run standalone inside `nicolaka/netshoot:v0.15` or on the jumphost,
where only the stdlib is available — see "Two execution tiers" below.

## Three physical environments — this shapes almost every design decision here

The project is built around three machines with very different capabilities,
and code changes must respect these constraints (see README.md "Los tres
entornos" for the full picture):

1. **Dev PC** (where `uv`-based generation happens) — no network access to
   Domain 3 at all. Everything under `src/generate_*.py` runs only here.
2. **Lab PC** — has `kubectl` configured directly against the Domain 3
   clusters, and can also reach the jumphost (e.g. via SSH). This is where
   `src/run_via_kubectl.sh` runs, and where the operator pastes the
   standalone script into a jumphost session.
3. **Jumphost / VM in Domain 3** — can reach Domain 2, but **file transfer
   to it is hard**; pasting a block of text into a terminal is the only easy
   channel. This is why `src/generate_standalone_script.py` exists: it
   bundles `run_probe.py`'s source, a filtered spec, and
   `connectivity-tests.toml` into heredocs inside one `.sh` file, so the
   whole thing (code + data + config) can be delivered by copy-pasting a
   single block, with no `scp` required.

Generated artifacts under `inputs/`, `manifests/servers/`, and `standalone/`
are **gitignored** (see `.gitignore`) because they contain real internal
IPs/topology of Domain 2 and Domain 3 — they are meant to be regenerated
locally or transferred out-of-band via OneDrive Web, never committed.

## Two execution tiers for the Python code

- **Generators** (`generate_test_spec.py`, `generate_server_manifests.py`,
  `generate_standalone_script.py`): run only on the Dev PC via `uv run`, may
  use `pyyaml` (the project's only dependency) and `tomllib` (stdlib,
  Python ≥3.11).
- **`run_probe.py`** (the actual TCP/UDP probe engine): must stay
  **stdlib-only** (`json`, `socket`, `subprocess`, `tomllib`, `argparse`,
  `re`, `errno`) because it executes inside `nicolaka/netshoot:v0.15` (no
  `pip install` available in Domain 3) or is embedded verbatim into the
  jumphost script. Do not add third-party imports here.

`connectivity-tests.toml` is read by both tiers for different purposes: the
top-level `port_protocol_pairing` key configures `generate_test_spec.py`'s
CSV expansion; the `[probe]` table configures `run_probe.py`'s UDP-ICMP-wait
calibration (via `--config`, optional — falls back to embedded defaults if
absent, so `run_probe.py` keeps working standalone).

## The spec: single source of truth

`inputs/connectivity-test-spec.yaml`/`.json` (generated, git-ignored) is what
every other script consumes. Each test case has:
- `direction`: `domain3_to_domain2` (Domain 3 is the client — the only
  direction that can be automated without Domain 2 cooperation) or
  `domain2_to_domain3` (Domain 3 is the server — needs manual testing from a
  Domain 2 client, see README section 3).
- `automatable`: `true` only for `domain3_to_domain2`.
- `source.type`: `"VM"` or `"K8s Cluster"` — determines which backend
  (`run_via_kubectl.sh` vs. the standalone jumphost script) should run it.
- `protocol`/`protocol_label`: normalized `tcp`/`udp` plus the original CSV
  label (e.g. "JDBC over mTLS" still classifies as `tcp`).

CSV rows expand into multiple test cases: IP lists/ranges (`a, b` or
`a.b.c.d-e.f.g.h`) and port lists always cross-product; when a row has the
*same* count of ports and protocols (e.g. 3 ports, 3 protocols), they pair
1:1 by position by default (`port_protocol_pairing = "one_to_one"` in
`connectivity-tests.toml`), not cross-product — this mirrors how the source
CSV rows are laid out. The YAML is hand-editable; after editing, run
`uv run src/generate_test_spec.py --from-yaml` to resync the JSON without
re-parsing the CSVs.

## Connectivity diagnosis logic (`run_probe.py`)

The core design question this script answers is: *when a probe fails, is it
a firewall problem or just a service that isn't deployed yet in Domain 2?*
It distinguishes three cases (documented in depth in README.md section 5 and
the "Interpretación de veredictos" table — read that before changing verdict
logic):

- **A** — TCP connection actively refused (`ConnectionRefusedError`/RST), or
  for UDP an ICMP port-unreachable observed via a connect+double-`send()`
  trick (`UDP_REFUSED_NETWORK_OPEN`) — strongest signal: network/firewall is
  open, only the service is missing.
- **B** — TCP times out but `ping` succeeds — weaker signal, still probably
  "service not deployed" but could also be selective port filtering.
- **C** — neither responds — inconclusive, suspect the firewall rule.

`tcp_check()` also distinguishes `EHOSTUNREACH`/`ENETUNREACH` ("no route to
host") from a silent timeout as an annotated sub-signal within B/C.
`traceroute_check()`'s raw output is summarized by `summarize_traceroute()`
to report the last hop that responded and whether it's the actual
destination. Any change to verdict names must be kept in sync across:
`src/run_probe.py`, `README.md` (both the decision table and the final
verdicts table), and `.claude/skills/run-domain3-connectivity-tests/SKILL.md`.

## Verifying changes

There's no test suite; verification is done by running things directly
(all of this works from the Dev PC without Domain 3 access):
- Build a minimal JSON spec by hand (a `{"tests": [...]}` list of objects
  with `id`, `direction`, `automatable`, `source`, `destination.ip`, `port`,
  `protocol`, `protocol_label` — see any generated
  `inputs/connectivity-test-spec.json` for the exact shape) and run
  `python3 src/run_probe.py --spec <file> --out <file>` against public IPs or
  `127.0.0.1` to exercise PASS/refused/timeout/UDP paths.
- `bash -n` any generated or edited shell script.
- Regenerate the standalone script and grep for the heredoc delimiters
  (`PROBE_PY_EOF`, `SPEC_JSON_EOF`, `PROBE_TOML_EOF`) to confirm embedding
  worked; `generate_standalone_script.py` already raises if embedded content
  collides with a delimiter.
- Validate generated K8s manifests with `yaml.safe_load_all`.

## Project skills

- `.claude/skills/generate-connectivity-spec` — regenerate the spec/manifests/
  standalone script after CSV or spec changes.
- `.claude/skills/run-domain3-connectivity-tests` — pick the right backend
  (kubectl vs. standalone script) per test case and interpret results.
