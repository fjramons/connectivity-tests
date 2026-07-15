---
name: create-local-vm
description: Brings up (or tears down) a local VM/jumphost stand-in (emulated container, or the real generated standalone script run on the laptop itself) for developing/testing run_probe.py and generate_standalone_script.py without a real Remote-cloud-domain-reachable jumphost. Use when validating changes to run_probe.py or the standalone script for "VM" source-type test cases.
---
# Create a local VM (real or emulated)

This emulates the jumphost/VM tier described in `CLAUDE.md`'s "Three
physical environments", so `src/run_probe.py` and
`src/generate_standalone_script.py`'s output can be exercised without a
real Remote-cloud-domain-reachable jumphost. There are two ways to
validate this locally -- both useful, for different things:

## Mode 1: "emulated" -- a persistent container (this skill's script)

`dev-env/vm.sh` manages a persistent container (`conntest-dev-vm`,
`nicolaka/netshoot:v0.15` -- the same image used everywhere else in this
repo) that runs `run_probe.py` **directly**, with no nested
`docker run --network host` wrapper. This is the most portable option
since it doesn't depend on host networking quirks.

```bash
dev-env/vm.sh up       # create/start the container, join it to the "kind" docker network if present
dev-env/vm.sh status    # read-only: container state, networks, IP
dev-env/vm.sh down      # remove the container
```

`up` is idempotent. Add `-y`/`--yes` to auto-confirm cleanup if
provisioning fails partway (default: asks interactively, defaulting to
"no" so you can inspect/debug instead of losing state).

No extra `--cap-add` is needed: Docker's default capability set already
includes `NET_RAW`, which is all `ping`/`traceroute`/`tcptraceroute` need
(the same is true of `manifests/netshoot-client-k8s.yaml`, which requests
no special capabilities either).

If the `kind` docker network already exists (i.e. `dev-env/cluster.sh up`
has run), the VM container is also connected to it, so it can reach both
the fake Remote-cloud-domain targets (`dev-env/targets.sh`) and, if
needed, cluster pods. If it doesn't exist yet, the container is still
useful standalone (e.g. against public IPs, per the existing
"Verifying changes" section in `CLAUDE.md`).

### Running a probe inside it (manual, mirrors `src/run_via_kubectl.sh`)

```bash
docker cp src/run_probe.py conntest-dev-vm:/tmp/run_probe.py
docker cp inputs/dev-local/connectivity-test-spec.json conntest-dev-vm:/tmp/spec.json
docker cp inputs/dev-local/connectivity-tests.toml conntest-dev-vm:/tmp/connectivity-tests.toml
docker exec conntest-dev-vm python3 /tmp/run_probe.py batch --spec /tmp/spec.json \
  --filter-source-type VM --out /tmp/result.log --config /tmp/connectivity-tests.toml
docker cp conntest-dev-vm:/tmp/result.log outputs/dev-local/logs/vm-emulated-$(date -u +%Y%m%dT%H%M%SZ).log
docker cp conntest-dev-vm:/tmp/result.json outputs/dev-local/logs/vm-emulated-<same-timestamp>.json
```

(`dev-env/vm.sh up` prints this same recipe with the current suite name
filled in.)

## Mode 2: "real" -- run the actual generated standalone script here

`src/generate_standalone_script.py`'s output already only needs Docker
(`docker run --network host ...nicolaka/netshoot:v0.15...`) -- nothing
else about a real jumphost is assumed. That means the laptop's own Docker
can run that exact artifact directly, with no wrapper needed:

```bash
uv run src/generate_standalone_script.py --suite dev-local
bash outputs/dev-local/standalone/local-cloud-domain-to-remote-cloud-domain-vm-tests.sh
```

This is the better choice when you specifically want to validate the
generated script itself (heredoc embedding, delimiter handling, the
outer bootstrap logic), not just `run_probe.py`'s probing behavior.

**Caveat**: `--network host` is fully supported on native Linux and on
Docker Engine running natively inside a WSL2 distro, but has had
inconsistent support under Docker Desktop's WSL2 integration depending on
version. Verify once before relying on it:

```bash
docker run --rm --network host nicolaka/netshoot:v0.15 ip addr
# should show the host's real interfaces, not a private VM-only one
```

If that doesn't look right, use Mode 1 (the emulated container) instead --
it doesn't depend on `--network host` at all.

## Tearing down

`dev-env/vm.sh down` removes the emulated VM container. Remember to also
tear down `dev-env/targets.sh down` and `dev-env/cluster.sh down` if you
brought those up as part of the same session -- see the
`create-local-k8s-cluster` skill.
