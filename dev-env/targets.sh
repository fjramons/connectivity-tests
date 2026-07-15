#!/usr/bin/env bash
# Fake "Remote cloud domain" destination containers for local development
# (dev-env/compose/fake-remote-targets-docker-compose.yml). Self-sufficient:
# creates the "kind" docker network (see ensure_kind_network() in
# lib/common.sh) if dev-env/cluster.sh hasn't run yet, so this works
# standalone for VM-only validation (dev-env/validate.sh --only vm) without
# needing a real K8s cluster -- if dev-env/cluster.sh up runs later, it
# reuses this same network rather than conflicting with it.
#
# Usage:
#   dev-env/targets.sh up [-y|--yes]
#   dev-env/targets.sh down [-y|--yes]
#   dev-env/targets.sh status
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

COMPOSE_FILE="$DEV_ENV_DIR/compose/fake-remote-targets-docker-compose.yml"
ENV_FILE="$DEV_ENV_DIR/compose/.env"

write_env_file() {
  local prefix
  prefix="$(discover_subnet_prefix)"
  echo "DEV_ENV_SUBNET_PREFIX=$prefix" >"$ENV_FILE"
}

cmd_up() {
  preflight_compose
  write_env_file
  log_info "Starting fake Remote-cloud-domain target containers..."
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
  log_ok "Fake targets are up."
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps >&2
}

cmd_down() {
  if [[ ! -f "$ENV_FILE" ]]; then
    log_info "No fake targets recorded as started, nothing to do."
    return 0
  fi
  preflight_compose
  log_info "Stopping fake Remote-cloud-domain target containers..."
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down
  rm -f "$ENV_FILE"
  log_ok "Fake targets torn down."
}

cmd_status() {
  if [[ ! -f "$ENV_FILE" ]]; then
    log_info "Fake targets are NOT running."
    return 0
  fi
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps >&2
}

main() {
  local subcommand="${1:-}"
  [[ $# -gt 0 ]] && shift
  parse_yes_flag "$@" >/dev/null # sets the global ASSUME_YES

  case "$subcommand" in
    up)     run_up_with_recovery cmd_down cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    *)
      echo "Usage: $0 {up|down|status} [-y|--yes]" >&2
      exit 1
      ;;
  esac
}

main "$@"
