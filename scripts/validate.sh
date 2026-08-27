#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_root"
live=false
while (($#)); do
  case $1 in
    --live) live=true; shift ;;
    -h|--help) printf '%s\n' 'Usage: validate.sh [--live]'; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
for script in bootstrap.sh scripts/*.sh; do
  bash -n "$script"
done
python3 -m unittest discover -s tests -v
scripts/validate-public-tree.sh
if [[ $live == true ]]; then
  [[ $(sudo -n id -u 2>/dev/null) == 0 ]] || {
    printf '%s\n' 'PASSWORDLESS_ROOT_CHECK=FAIL' >&2
    exit 1
  }
  scripts/validate-memory.sh
  scripts/validate-chrome-mcp.sh
  command -v hermes >/dev/null 2>&1 && hermes mcp list
  command -v hermes-health >/dev/null 2>&1 && hermes-health
fi
printf 'VALIDATION=PASS\n'
