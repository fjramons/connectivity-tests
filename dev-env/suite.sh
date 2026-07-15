#!/usr/bin/env bash
# Copies the synthetic reference test plan (dev-env/reference-suite/, git-
# tracked) into inputs/<suite>/ (gitignored, like every suite's CSVs),
# substituting the placeholder IP tokens for the addresses actually in use
# on this machine (see dev-env/reference-suite/NOTES.md). Requires
# dev-env/cluster.sh up to have run first (that's what records the subnet
# prefix this substitution needs).
#
# inputs/<suite>/ is ALWAYS derived from dev-env/reference-suite/ -- edit
# the CSVs there and re-run sync, never edit inputs/<suite>/ directly (it's
# overwritten every time).
#
# Usage:
#   dev-env/suite.sh sync [--suite dev-local]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

REFERENCE_DIR="$DEV_ENV_DIR/reference-suite"

cmd_sync() {
  local suite="$1"
  local prefix
  prefix="$(read_subnet_prefix)"
  local target_dir="$ROOT_DIR/inputs/$suite"
  mkdir -p "$target_dir"

  log_info "Syncing $REFERENCE_DIR/ -> $target_dir/ (prefix ${prefix}.0/24)..."
  for src in "$REFERENCE_DIR"/*.csv; do
    local filename
    filename="$(basename "$src")"
    sed \
      -e "s/__DEV_ENV_LOCAL_SERVER_1__/${prefix}.200/g" \
      -e "s/__DEV_ENV_LOCAL_SERVER_2__/${prefix}.201/g" \
      -e "s/__DEV_ENV_TARGET_OPEN__/${prefix}.210/g" \
      -e "s/__DEV_ENV_TARGET_REFUSED__/${prefix}.211/g" \
      -e "s/__DEV_ENV_TARGET_FILTERED__/${prefix}.212/g" \
      -e "s/__DEV_ENV_TARGET_UNREACHABLE__/${prefix}.230/g" \
      "$src" >"$target_dir/$filename"
  done
  cp "$REFERENCE_DIR/connectivity-tests.toml" "$target_dir/connectivity-tests.toml"

  log_ok "Synced into $target_dir/ (derived -- do not hand-edit, edit $REFERENCE_DIR/ and re-run sync instead)."
  log_info "Next: uv run src/generate_test_spec.py --suite $suite"
}

main() {
  local subcommand="${1:-}"
  [[ $# -gt 0 ]] && shift
  local suite="dev-local"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --suite) suite="$2"; shift 2 ;;
      *) echo "❌ Unknown argument: $1" >&2; exit 1 ;;
    esac
  done

  case "$subcommand" in
    sync) cmd_sync "$suite" ;;
    *)
      echo "Usage: $0 sync [--suite dev-local]" >&2
      exit 1
      ;;
  esac
}

main "$@"
