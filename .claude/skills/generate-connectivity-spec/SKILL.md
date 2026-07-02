---
name: generate-connectivity-spec
description: Regenerates the Remote cloud domain <-> Local cloud domain connectivity test specification from the CSVs in inputs/, and the derived manifests/scripts. Use when the CSVs in inputs/ change or the spec YAML has been edited by hand.
---
# Regenerate the connectivity test specification

This project validates connectivity between Remote cloud domain and Local
cloud domain from two CSVs in `inputs/` (`... Clients.csv` and `... Servers.csv`).
The whole regeneration flow runs on the dev PC with `uv`
(`uv sync` once, to have PyYAML available).

## When to use this skill

- The CSVs in `inputs/` have changed (new version of the firewall matrix).
- `inputs/connectivity-test-spec.yaml` has been edited by hand (for example, to
  fix an `unresolved` entry) and the JSON needs to be resynced.
- The `remote_cloud_domain_to_local_cloud_domain` destinations have changed and
  the server manifests or the self-contained script for the jumphost need to
  be regenerated.

## Steps

1. **Regenerate the spec from the CSVs** (normal case, when the CSVs change):
   ```bash
   uv run src/generate_test_spec.py
   ```
   Review the printed summary: number of `local_cloud_domain_to_remote_cloud_domain`
   cases (automatable) vs. `remote_cloud_domain_to_local_cloud_domain` (manual), and
   how many ended up in `unresolved` (should be 0 unless the CSV has
   incomplete data).

2. **If the YAML was edited by hand instead**, don't repeat step 1 (you'd lose the
   edit): resync only the JSON:
   ```bash
   uv run src/generate_test_spec.py --from-yaml
   ```

3. **Regenerate the server manifests** (one per unique
   `remote_cloud_domain_to_local_cloud_domain` destination, to deploy in Local cloud
   domain and have Remote cloud domain test them):
   ```bash
   uv run src/generate_server_manifests.py
   ```

4. **Regenerate the self-contained script for the jumphost/VM** (
   `local_cloud_domain_to_remote_cloud_domain` tests with `source.type == "VM"`):
   ```bash
   uv run src/generate_standalone_script.py
   ```

5. Remember that these artifacts (`inputs/connectivity-test-spec.*`, `manifests/`,
   `standalone/`) must be moved manually to the lab PC via OneDrive
   Web — there's no automated way to do it from here.

See `README.md` for details on each backend (K8s vs. VM/jumphost) and how
test verdicts are interpreted.
