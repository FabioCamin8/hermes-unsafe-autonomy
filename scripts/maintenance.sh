#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
hermes_home=${HERMES_HOME:-${HOME}/.hermes}
export HERMES_HOME="$hermes_home"
vault_cli=${HERMES_VAULT_CLI:-"$HOME/.local/bin/hermes-vault"}
[[ -x $vault_cli ]] || { printf 'missing Hermes Vault CLI: %s\n' "$vault_cli" >&2; exit 1; }
exec "$vault_cli" maintenance --keep "${HERMES_VAULT_KEEP:-7}"
