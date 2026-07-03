---
name: generate-connectivity-spec
description: Regenerates the Remote cloud domain <-> Local cloud domain connectivity test specification from the CSVs in inputs/<suite>/, and the derived manifests/scripts. Use when the CSVs in inputs/<suite>/ change or the spec YAML has been edited by hand.
---
# Regenerate the connectivity test specification

This project validates connectivity between Remote cloud domain and Local
cloud domain from two CSVs in `inputs/<suite>/` (`... Clients.csv` and `... Servers.csv`).
The whole regeneration flow runs on the dev PC with `uv`
(`uv sync` once, to have PyYAML available).

## Suites

Every command below requires a suite, via `--suite <name>` or by exporting
`TEST_SUITE=<name>` once per shell session (the flag wins if both are set).
The same suite name resolves every path consistently: `inputs/<name>/`,
`outputs/<name>/manifests/servers/`, `outputs/<name>/standalone/`, and
optionally `inputs/<name>/connectivity-tests.toml` if that suite needs a
config override (most suites don't — they use the generic
`connectivity-tests.toml` at the repo root). `ls inputs/` lists the
suites that currently exist on disk.

## When to use this skill

- The CSVs in `inputs/<suite>/` have changed (new version of the firewall matrix).
- `inputs/<suite>/connectivity-test-spec.yaml` has been edited by hand (for example, to
  fix an `unresolved` entry) and the JSON needs to be resynced.
- The `remote_cloud_domain_to_local_cloud_domain` destinations have changed and
  the server manifests or the self-contained script for the jumphost need to
  be regenerated.

## Steps

1. **Regenerate the spec from the CSVs** (normal case, when the CSVs change):
   ```bash
   uv run src/generate_test_spec.py --suite <name>
   ```
   Review the printed summary: number of `local_cloud_domain_to_remote_cloud_domain`
   cases (automatable) vs. `remote_cloud_domain_to_local_cloud_domain` (manual), and
   how many ended up in `unresolved` (should be 0 unless the CSV has
   incomplete data).

2. **If the YAML was edited by hand instead**, don't repeat step 1 (you'd lose the
   edit): resync only the JSON:
   ```bash
   uv run src/generate_test_spec.py --suite <name> --from-yaml
   ```

3. **Regenerate the server manifests** (one per unique
   `remote_cloud_domain_to_local_cloud_domain` destination, to deploy in Local cloud
   domain and have Remote cloud domain test them):
   ```bash
   uv run src/generate_server_manifests.py --suite <name>
   ```

4. **Regenerate the self-contained script for the jumphost/VM** (
   `local_cloud_domain_to_remote_cloud_domain` tests with `source.type == "VM"`):
   ```bash
   uv run src/generate_standalone_script.py --suite <name>
   ```

5. Remember that these artifacts (`inputs/<name>/connectivity-test-spec.*`,
   `outputs/<name>/manifests/servers/`, `outputs/<name>/standalone/`) must be moved manually
   to the lab PC via OneDrive Web — there's no automated way to do it from
   here.

See `README.md` for details on each backend (K8s vs. VM/jumphost) and how
test verdicts are interpreted.
