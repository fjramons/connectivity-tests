---
name: validate-with-local-dev-env
description: Runs the whole local generate->deploy->test->report pipeline against the dev-local suite with one command (dev-env/validate.sh), to validate a change to this repo's generators/run_probe.py end-to-end without manually sequencing dev-env/{cluster,targets,vm,suite}.sh. Use after changing generate_server_manifests.py, generate_test_spec.py, run_probe.py, run_via_kubectl.sh, or generate_report.py.
---
# Validate a change with the local dev environment

`dev-env/validate.sh` composes the pieces from the `create-local-k8s-cluster`
and `create-local-vm` skills (cluster/targets/VM lifecycle) with the
generation/deploy/test/report pipeline, so validating a code change is one
command instead of manually re-running each step from README.md's "Local
development environment" section.

**Reminder**: this validates the tooling/automation end-to-end, not real
firewall rules -- kind enforces no NetworkPolicies. See
`dev-env/reference-suite/NOTES.md` for exactly which test case is expected
to produce which verdict; a mismatch after your code change is a real
regression, not noise.

## Usage

```bash
dev-env/validate.sh run    [--suite dev-local] [--only k8s|vm|both] [-y|--yes]
dev-env/validate.sh down   [--suite dev-local] [-y|--yes]
dev-env/validate.sh status [--suite dev-local]
```

`run` (default `--only both`):
1. Brings up whatever infra `--only` needs (idempotent -- safe to re-run
   without tearing down first).
2. Syncs `dev-env/reference-suite/` into `inputs/<suite>/`.
3. Regenerates the spec (`generate_test_spec.py`).
4. `--only k8s`/`both` only: regenerates + applies the local server
   manifests (`generate_server_manifests.py` + `kubectl apply -f
   outputs/<suite>/manifests/servers/local/`), proving the MetalLB
   LoadBalancer mechanism works -- these cases are never actually probed
   by `run_probe.py` (always `SKIPPED_MANUAL_TEST_REQUIRED`, same as prod).
5. Runs the automated tests: `--only k8s`/`both` via
   `src/run_via_kubectl.sh`; `--only vm`/`both` via the new
   `dev-env/run-vm-tests.sh` (the emulated-VM equivalent, `docker cp`/`exec`
   instead of `kubectl cp`/`exec`).
6. Regenerates the consolidated report (`generate_report.py`) and prints
   its path.
7. Prints a reminder + the exact `dev-env/validate.sh down --suite <suite>`
   command -- **infra is left running by design** (iterative development;
   tearing down would force a kind+MetalLB rebuild for every small
   re-check). Only tear down once the development being validated is
   actually finished.

## When to use `--only`

- `--only vm`: iterating on `run_probe.py` itself, or the VM-sourced path
  in general -- no K8s cluster needed at all. `dev-env/targets.sh` creates
  the `kind` docker network itself in this mode (see
  `ensure_kind_network()` in `dev-env/lib/common.sh`), so this genuinely
  skips the cluster, not just the client pod.
- `--only k8s`: iterating on `generate_server_manifests.py`,
  `run_via_kubectl.sh`, or the MetalLB/LoadBalancer mechanism -- no VM
  container needed.
- `--only both` (default): anything that could plausibly affect either
  path, or you're not sure which.

## `down` and `status`

`down` tears down the VM, fake targets, and cluster in that order
(mirrors `create-local-vm`/`create-local-k8s-cluster`'s own teardown).
`status` shows the combined state of all three plus whether
`inputs/<suite>/` looks synced.

## When this skill isn't the right level

For anything more fine-grained than the composed flow -- debugging a
single step, wanting the cluster up without redeploying manifests, testing
just the standalone script's own bootstrap logic -- drop down to the
`create-local-k8s-cluster` and `create-local-vm` skills and the manual
steps in README.md, which this skill's `run` is just a composition of.
