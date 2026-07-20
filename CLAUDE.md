# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Tooling to validate connectivity and firewall rules between "Remote cloud
domain" and "Local cloud domain", driven by a firewall rule matrix delivered as two CSV
files in `inputs/<suite>/`. It generates a normalized test spec, K8s/Compose
manifests, and scripts to actually run the connectivity checks — both
automated and fully-manual fallbacks (see README.md sections 2 and 4, and
the "Step-by-step manual diagnosis" appendix, for the manual procedures and
how connectivity failures are diagnosed).

Multiple independent **test suites** can coexist on disk under named
subfolders: `inputs/<suite>/` (CSVs + generated spec, optionally its own
`connectivity-tests.toml` override) and `outputs/<suite>/` (generated
server manifests, standalone script, run logs — see "Suites" below for the
exact subtree). Every entry point requires a suite, selected via `--suite
<name>` (or the equivalent flag on `run_via_kubectl.sh`) or the
`TEST_SUITE` environment variable.

No test framework is configured; there is no `pytest`/CI. Validation of
changes is done by running the scripts directly (see "Verifying changes"
below).

## Setup and commands

```bash
uv sync                                              # create .venv with pyyaml (only dep)
uv run src/generate_test_spec.py --suite NAME         # CSV -> inputs/NAME/connectivity-test-spec.{yaml,json}
uv run src/generate_server_manifests.py --suite NAME  # spec -> outputs/NAME/manifests/servers/local/<slug>-k8s.yaml + .../remote/<slug>-k8s.yaml (one per unique destination, per direction)
uv run src/generate_standalone_script.py --suite NAME # spec + run_probe.py -> outputs/NAME/standalone/*.sh (self-contained, for the jumphost)
uv run src/generate_report.py --suite NAME            # spec + outputs/NAME/logs/*.json -> outputs/NAME/logs/summary-report.{txt,html}
uv run python3 -m py_compile src/*.py                 # syntax-check all Python scripts
bash -n <script>.sh                                   # syntax-check generated/hand-written shell scripts
```

`--suite NAME` is required on all four generators (and on
`src/run_via_kubectl.sh`, as `--suite`); it can also be set once per shell
session via `export TEST_SUITE=NAME` instead of repeating the flag — see
"Suites" below.

`src/run_probe.py` is invoked with plain `python3` (not `uv run`) because it
must run standalone inside `nicolaka/netshoot:v0.15` or on the jumphost,
where only the stdlib is available — see "Two execution tiers" below.

## Suites

Every entry point (`generate_test_spec.py`, `generate_server_manifests.py`,
`generate_standalone_script.py`, `generate_report.py`, `run_via_kubectl.sh`)
resolves a suite name as: explicit `--suite <name>` flag, else the `TEST_SUITE` environment
variable, else the `default` suite (`inputs/default/`), printed as an `ℹ️`
notice so a run never silently lands in the wrong suite unnoticed. If the
resolved suite's `inputs/<name>/` folder doesn't exist, every entry point
fails fast with a clear error listing the suites that do exist and how to
pick one — no entry point ever operates against a nonexistent suite. The
same suite name is reused, unchanged, to resolve every path across all
five entry points:

- `inputs/<name>/` (source CSVs and the generated spec,
  `connectivity-test-spec.{yaml,json}`)
- `outputs/<name>/manifests/servers/local/` (mock listeners for
  `remote_cloud_domain_to_local_cloud_domain` destinations — Local-cloud-
  domain-owned, deployed there directly via `kubectl`)
- `outputs/<name>/manifests/servers/remote/` (mock listeners for
  `local_cloud_domain_to_remote_cloud_domain` destinations — Remote-cloud-
  domain-owned; we have no deploy access there, so these are handed off to
  the Remote-cloud-domain team instead)
- `outputs/<name>/standalone/` (the self-contained jumphost script)
- `outputs/<name>/logs/` (run logs: one `.log` + structured `.json`
  companion per `run_probe.py batch` run, plus the consolidated
  `summary-report.{txt,html}` produced by `generate_report.py` from all
  of them — see "Verifying changes" and README.md section 5)
- optionally `inputs/<name>/connectivity-tests.toml` (suite-specific config
  override — see "Config resolution" below)

So switching suites is changing one flag/variable, not several. `ls
inputs/` lists the suites that currently exist on disk. Suite/config
resolution (`resolve_suite()`, `check_suite_exists()`,
`resolve_config_path()`) lives in one shared `src/suite_common.py`,
imported by all four Python generators — this is the one exception to
this repo's usual style of small independent scripts with no shared
module, justified because the three functions were previously
byte-for-byte duplicated across three files and a fourth generator
(`generate_report.py`) needed the same logic again. `run_probe.py` and the
hand-written client manifests (`manifests/netshoot-client-*`) remain
suite-agnostic by design (see below) and do not import `suite_common`.

## Three physical environments — this shapes almost every design decision here

The project is built around three machines with very different capabilities,
and code changes must respect these constraints (see README.md "The three
environments" for the full picture):

1. **Dev PC** (where `uv`-based generation happens) — no network access to
   Local cloud domain at all. Everything under `src/generate_*.py` runs only here.
2. **Lab PC** — has `kubectl` configured directly against the Local cloud
   domain clusters, and can also reach the jumphost (e.g. via SSH). This is
   where `src/run_via_kubectl.sh` runs, and where the operator pastes the
   standalone script into a jumphost session.
3. **Jumphost / VM in Local cloud domain** — can reach Remote cloud
   domain, but **file transfer to it is hard**; pasting a block of text into
   a terminal is the only easy channel. This is why
   `src/generate_standalone_script.py` exists: it bundles `run_probe.py`'s
   source, a filtered spec, and `inputs/<suite>/connectivity-tests.toml` into heredocs
   inside one `.sh` file, so the whole thing (code + data + config) can be
   delivered by copy-pasting a single block, with no `scp` required. The
   run's results travel back the same way, in reverse: the script prints
   both the `.log` and its `.json` companion between copy-paste markers for
   the operator to save locally under matching filenames.

Generated artifacts — the CSVs and generated spec under `inputs/<suite>/`,
and everything under `outputs/<suite>/` (manifests, standalone script, run
logs) — are **gitignored** (see `.gitignore`, which lists specific file
patterns rather than blanket-excluding `inputs/<suite>/` so that the config
file below can stay tracked) because they contain real internal
IPs/topology of Remote cloud domain and Local cloud domain — they are meant
to be regenerated locally or transferred out-of-band via OneDrive Web,
never committed. `inputs/<suite>/connectivity-tests.toml` is the exception:
it holds no topology/IP data (just pairing/namespace/probe-calibration
config), so it **is** git-tracked when a suite needs to override the
generic default. The generic `connectivity-tests.toml` at the repo root is
itself **gitignored** (only `connectivity-tests.toml.template` is
tracked) — see "Config resolution" below for why that's safe to do without
losing anything on a fresh clone.

## Two execution tiers for the Python code

- **Generators** (`generate_test_spec.py`, `generate_server_manifests.py`,
  `generate_standalone_script.py`, `generate_report.py`): run only on the
  Dev PC via `uv run`, may use `pyyaml` (the project's only dependency) and
  `tomllib` (stdlib, Python ≥3.11). `generate_report.py` is the exception
  that doesn't need either — it only reads JSON/the spec, both stdlib.
- **`run_probe.py`** (the actual TCP/UDP probe engine): must stay
  **stdlib-only** (`json`, `socket`, `subprocess`, `tomllib`, `argparse`,
  `re`, `errno`) because it executes inside `nicolaka/netshoot:v0.15` (no
  `pip install` available in Local cloud domain) or is embedded verbatim into the
  jumphost script. Do not add third-party imports here.

The config file (`connectivity-tests.toml`, see "Config resolution" below)
is read by both tiers for different purposes: the top-level
`port_protocol_pairing` key configures `generate_test_spec.py`'s CSV
expansion; the top-level `namespace` key (default `"default"`) is used by
`generate_server_manifests.py` only to print/document the suggested
`kubectl apply -n <namespace>` deploy command — it is never baked into the
generated YAML's `metadata`, deploying with an explicit `-n` is preferred
for clarity; the top-level `metallb_ip_mechanism` key (default `"both"`,
also overridable per-run with `--metallb-ip-mechanism`) controls whether
`generate_server_manifests.py`'s generated Service sets
`spec.loadBalancerIP`, the `metallb.io/loadBalancerIPs` annotation, or both
— current MetalLB releases reject a Service with both set at once, older
ones needed one or the other depending on version, so this is
suite-configurable rather than hardcoded (see
`connectivity-tests.toml.template` and `dev-env/reference-suite/
connectivity-tests.toml` for a suite that needs `"annotation"`); the
`[probe]` table configures `run_probe.py`'s UDP-ICMP-wait
calibration (via `--config`, optional — falls back to embedded defaults if
absent, so `run_probe.py` keeps working standalone). `run_probe.py` itself
takes `--config` as a plain path with no suite awareness — the caller
(`run_via_kubectl.sh` or the standalone script) resolves the right file
before invoking it.

## Config resolution

Each of `resolve_config_path()` (duplicated in the three Python generators)
and the equivalent logic in `run_via_kubectl.sh` resolves the config file
for a suite in this order: (1) an explicit `--config` override, (2)
`inputs/<suite>/connectivity-tests.toml` if that suite has its own
override, (3) otherwise the generic `connectivity-tests.toml` at the repo
root — which is auto-created from `connectivity-tests.toml.template`
(git-tracked, the canonical starting point for both the generic file and
any per-suite override) the first time it's needed and missing. This means
most suites need no config file of their own at all; only suites that
actually need a different `namespace`/pairing/probe calibration get an
`inputs/<suite>/connectivity-tests.toml`.

## The spec: single source of truth

`inputs/<suite>/connectivity-test-spec.yaml`/`.json` (generated, git-ignored)
is what every other script consumes for that suite. Each test case has:
- `direction`: `local_cloud_domain_to_remote_cloud_domain` (Local cloud
  domain is the client — the only direction that can be automated without
  Remote cloud domain cooperation) or `remote_cloud_domain_to_local_cloud_domain`
  (Local cloud domain is the server — needs manual testing from a Remote
  cloud domain client, see README section 2).
- `automatable`: `true` only for `local_cloud_domain_to_remote_cloud_domain`.
- `source.type`: `"VM"` or `"K8s Cluster"` — determines which backend
  (`run_via_kubectl.sh` vs. the standalone jumphost script) should run it.
- `protocol`/`protocol_label`: normalized `tcp`/`udp` plus the original CSV
  label (e.g. "JDBC over mTLS" still classifies as `tcp`).

CSV rows expand into multiple test cases: IP lists/ranges (`a, b` or
`a.b.c.d-e.f.g.h`) and port lists always cross-product; when a row has the
*same* count of ports and protocols (e.g. 3 ports, 3 protocols), they pair
1:1 by position by default (`port_protocol_pairing = "one_to_one"` in
`inputs/<suite>/connectivity-tests.toml`), not cross-product — this mirrors how the
source CSV rows are laid out. The YAML is hand-editable; after editing, run
`uv run src/generate_test_spec.py --suite <name> --from-yaml` to resync the
JSON without re-parsing the CSVs.

## Connectivity diagnosis logic (`run_probe.py`)

The core design question this script answers is: *when a probe fails, is it
a firewall problem or just a service that isn't deployed yet in Remote
cloud domain?*
It distinguishes three cases (documented in depth in README.md's
"Step-by-step manual diagnosis" appendix and
the "Verdict interpretation" table — read that before changing verdict
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
verdicts table), and `.claude/skills/run-local-cloud-domain-connectivity-tests/SKILL.md`.

Every diagnostic step (`ping`, `traceroute`/`tcptraceroute`, and the raw
TCP connect/UDP send themselves) is logged with an explicit `COMMAND:`
line showing the exact invocation (real argv for the subprocess-based
tools; a synthesized-but-labeled-as-such parameter line for TCP/UDP, which
use raw sockets, not a CLI tool) — see `run_test()`'s `log_command()`
helper. `run_test()`/`run_skipped()` also return a structured per-test
record (verdict, comment, commands, full detail text) that `cmd_batch()`
collects and writes as a `.json` companion next to `--out`'s `.log`, for
`generate_report.py` to consume later.

`generate_report.py --suite NAME` merges every `outputs/<suite>/logs/*.json`
(by test id, most recent `finished_at` wins — there's normally at least
one file per backend, K8s and VM, since each only covers the tests
originating from that `source.type`) against the suite's spec and writes
`outputs/<suite>/logs/summary-report.{txt,html}`. A test with
`automatable: false` and no matching JSON record is reported as
`SKIPPED_MANUAL_TEST_REQUIRED` **synthesized directly from the spec**, not
from an actual logged record — with the current `--filter-source-type`
usage in both backends, `remote_cloud_domain_to_local_cloud_domain` tests
(`source.type` is always `None`) never actually reach `run_skipped()` in
practice, so don't assume a real log record exists for them. A test that's
`automatable: true` but has no matching JSON record anywhere is reported
as `NOT_RUN_YET` — a report-only status, not one of `run_probe.py`'s own
verdicts, so it's intentionally absent from `VERDICT_ICON`.

## Verifying changes

There's no test suite; verification is done by running things directly
(all of this works from the Dev PC without Local cloud domain access):
- Build a minimal JSON spec by hand (a `{"tests": [...]}` list of objects
  with `id`, `direction`, `automatable`, `source`, `destination.ip`, `port`,
  `protocol`, `protocol_label` — see any generated
  `inputs/<suite>/connectivity-test-spec.json` for the exact shape) and run
  `python3 src/run_probe.py --spec <file> --out <file>` against public IPs or
  `127.0.0.1` to exercise PASS/refused/timeout/UDP paths.
- `bash -n` any generated or edited shell script.
- Regenerate the standalone script and grep for the heredoc delimiters
  (`PROBE_PY_EOF`, `SPEC_JSON_EOF`, `PROBE_TOML_EOF`) to confirm embedding
  worked; `generate_standalone_script.py` already raises if embedded content
  collides with a delimiter. Also grep for the `RESULTS_JSON START`/`END`
  markers to confirm the JSON companion is dumped alongside the log.
- Validate generated K8s manifests with `yaml.safe_load_all`.
- Validate `run_probe.py batch`'s `.json` companion with
  `python3 -c "import json,sys; json.load(open(sys.argv[1]))"`, then place
  it (and a spec covering the same test ids) under a scratch
  `inputs/<suite>/` / `outputs/<suite>/logs/` and run
  `uv run src/generate_report.py --suite <suite>` to confirm
  `summary-report.txt` and `.html` render all four statuses (`PASS`-like,
  weak/inconclusive with a comment, `SKIPPED_MANUAL_TEST_REQUIRED`, and
  `NOT_RUN_YET` for an untested id) and that the HTML opens/expands
  correctly in a browser.
- After touching `src/suite_common.py`, re-run all four generators against
  an existing suite to confirm the shared suite/config resolution behaves
  identically (same error messages for an invalid/nonexistent suite).
- For changes to `src/generate_server_manifests.py`, `src/run_via_kubectl.sh`,
  `src/run_probe.py`, or `src/generate_standalone_script.py`, prefer
  exercising them against the real thing rather than only the scratch
  fixtures above: `dev-env/` (see below) stands up a real local K8s cluster
  and VM stand-in with real `LoadBalancer` Services and real TCP/UDP
  refusal/timeout behavior, using the git-tracked `dev-env/reference-suite/`
  synced into the `dev-local` suite.

## Local development environment (`dev-env/`)

`dev-env/` is developer-only tooling — not a test framework, doesn't
change the "no test suite" statement above — that emulates the two
physical capabilities described in "Three physical environments" (a K8s
cluster with `LoadBalancer` Services, and a VM/jumphost) locally, entirely
in Docker, so changes can be validated end-to-end without the real Lab
PC/jumphost. `dev-env/cluster.sh` (kind + MetalLB) and `dev-env/vm.sh`
(emulated VM container) are independent, separately-testable capabilities;
`dev-env/targets.sh` (fake Remote-cloud-domain destinations) and
`dev-env/suite.sh` (syncs the git-tracked `dev-env/reference-suite/` into
`inputs/dev-local/`) support both. Every `kind`/`kubectl` call here uses an
isolated `KUBECONFIG` (`dev-env/.kubeconfig`, gitignored) — it never reads
or writes `~/.kube/config` or any other system/user kubeconfig; see
`dev-env/env.sh` to point your own shell's `kubectl` at it too. See
README.md "Local development environment" for full usage, and note its
important caveat: this validates the tooling, not real firewall rules —
kind enforces no NetworkPolicies.

`dev-env/validate.sh` composes all of the above (bring up whatever
`--only k8s`/`vm`/`both` needs, sync the suite, regenerate spec/manifests,
apply, run both automated test paths via `src/run_via_kubectl.sh` and the
new `dev-env/run-vm-tests.sh`, regenerate the report) into one `run`/
`down`/`status` command, so validating a change doesn't require manually
re-sequencing the individual scripts. `run` deliberately leaves everything
running afterwards (iterative development, not one-shot CI) — only `down`
tears it down, and `run` always prints that exact command as a closing
reminder.

## Project skills

- `.claude/skills/generate-connectivity-spec` — regenerate the spec/manifests/
  standalone script after CSV or spec changes.
- `.claude/skills/run-local-cloud-domain-connectivity-tests` — pick the right backend
  (kubectl vs. standalone script) per test case and interpret results.
- `.claude/skills/create-local-k8s-cluster` — bring up/tear down the local
  kind + MetalLB cluster (`dev-env/cluster.sh`).
- `.claude/skills/create-local-vm` — bring up/tear down the local VM/jumphost
  stand-in (`dev-env/vm.sh`), real or emulated.
- `.claude/skills/validate-with-local-dev-env` — run the whole pipeline
  end-to-end in one command (`dev-env/validate.sh`).
