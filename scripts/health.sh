#!/usr/bin/env bash
set -uo pipefail

umask 077

hermes_home=${HERMES_HOME:-${HOME}/.hermes}
hermes_bin=${HERMES_BIN:-${HOME}/.local/bin/hermes}
vault_cli=${HERMES_VAULT_CLI:-${HOME}/.local/bin/hermes-vault}
probe=${HERMES_MCP_PROBE:-${HOME}/.local/bin/hermes-mcp-probe}
chrome_command=${CHROME_DEVTOOLS_MCP_COMMAND:-${HOME}/.local/bin/chrome-devtools-mcp}
codex_command=${CODEX_COMMAND:-${HOME}/.local/bin/codex}
graphical_wrapper=${HERMES_GRAPHICAL_WRAPPER:-${HOME}/.local/bin/hermes-graphical}
graphical_env=${HERMES_GRAPHICAL_ENV_FILE:-${HOME}/.config/hermes/graphical-session.env}
cdp_port=${HERMES_CDP_PORT:-9222}
if ! [[ $cdp_port =~ ^[0-9]+$ ]] || ((10#$cdp_port < 1 || 10#$cdp_port > 65535)); then
  cdp_port=9222
fi
cdp_url="http://127.0.0.1:$cdp_port"

json_field() {
  local field=$1 payload=$2
  python3 -c '
import json
import sys

field = sys.argv[1]
try:
    value = json.load(sys.stdin).get(field)
except (TypeError, ValueError, OSError):
    value = None
if value is None:
    print("UNKNOWN")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
' "$field" <<<"$payload"
}

print_status() {
  printf '%-28s %s\n' "$1:" "$2"
}

probe_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if "$@"; then
      return 0
    fi
    (( attempt < 3 )) && sleep 1
  done
  return 1
}

gateway=FAIL
if systemctl --user is-active --quiet hermes-gateway.service 2>/dev/null; then
  gateway=OK
fi

vault_payload=
provider=FAIL
vault_database=FAIL
sqlite_integrity=FAIL
pending_writes=UNKNOWN
last_durable_turn=UNKNOWN
last_checkpoint=UNKNOWN
last_backup=UNKNOWN
if [[ -x $vault_cli ]]; then
  vault_payload=$("$vault_cli" status --json 2>/dev/null)
fi
if [[ -n $vault_payload ]]; then
  provider_value=$(json_field provider "$vault_payload")
  integrity_value=$(json_field integrity_ok "$vault_payload")
  sqlite_value=$(json_field sqlite_integrity "$vault_payload")
  pending_writes=$(json_field pending_writes "$vault_payload")
  last_durable_turn=$(json_field last_durable_turn "$vault_payload")
  last_checkpoint=$(json_field last_checkpoint "$vault_payload")
  last_backup=$(json_field last_backup "$vault_payload")
  [[ $provider_value == hermes_vault ]] && provider=OK
  [[ $integrity_value == true ]] && vault_database=OK
  [[ $sqlite_value == ok ]] && sqlite_integrity=OK
fi
if [[ $last_backup == UNKNOWN && -d $hermes_home/backups ]]; then
  latest_backup=$(find "$hermes_home/backups" -mindepth 1 -maxdepth 1 \
    -type d -name '*-*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n 1)
  if [[ -n $latest_backup ]]; then
    last_backup=$(basename "${latest_backup#* }")
  fi
fi

cdp=FAIL
if curl -fsS --max-time 3 "$cdp_url/json/version" >/dev/null 2>&1; then
  cdp=OK
fi

exposure=NOT_LISTENING
listeners=$(ss -ltnH 2>/dev/null | awk -v port=":$cdp_port" '$4 ~ port "$" {print $4}')
if [[ -n $listeners ]]; then
  exposure=LOOPBACK
  while IFS= read -r address; do
    case $address in
      "127.0.0.1:$cdp_port"|"[::1]:$cdp_port") ;;
      *) exposure=UNSAFE ;;
    esac
  done <<<"$listeners"
fi

chrome_mcp=FAIL
if [[ -x $probe && -x $chrome_command ]] && \
  "$probe" "$chrome_command" --exercise-browser -- \
    "--browser-url=$cdp_url" \
    --no-usage-statistics --no-performance-crux >/dev/null 2>&1; then
  chrome_mcp=OK
fi

codex_mcp=FAIL
if [[ -x $probe && -x $codex_command ]] && \
  probe_with_retry "$probe" "$codex_command" --timeout 60 -- mcp-server >/dev/null 2>&1; then
  codex_mcp=OK
fi

cua=FAIL
if [[ -x $hermes_bin && -x $graphical_wrapper && -r $graphical_env ]] && \
  "$graphical_wrapper" "$hermes_bin" computer-use doctor >/dev/null 2>&1; then
  cua=OK
elif [[ -x $hermes_bin ]] && "$hermes_bin" computer-use status >/dev/null 2>&1; then
  cua=DEGRADED
fi

unsafe_root=DISABLED
if command -v sudo >/dev/null 2>&1 && [[ $(sudo -n id -u 2>/dev/null) == 0 ]]; then
  unsafe_root=ENABLED
fi

print_status 'Hermes gateway' "$gateway"
print_status 'Memory provider' "$provider"
print_status 'Vault database' "$vault_database"
print_status 'SQLite integrity' "$sqlite_integrity"
print_status 'Pending writes' "$pending_writes"
print_status 'Last durable turn' "$last_durable_turn"
print_status 'Last checkpoint' "$last_checkpoint"
print_status 'Last backup' "$last_backup"
print_status 'Chrome CDP' "$cdp"
print_status 'Chrome MCP' "$chrome_mcp"
print_status 'Codex MCP' "$codex_mcp"
print_status 'CUA' "$cua"
print_status 'Unsafe root mode' "$unsafe_root"
print_status 'CDP exposure' "$exposure"

overall=PASS
for required in "$gateway" "$provider" "$vault_database" "$sqlite_integrity" \
  "$cdp" "$chrome_mcp" "$codex_mcp" "$unsafe_root"; do
  [[ $required == OK || $required == ENABLED ]] || overall=FAIL
done
[[ $exposure == LOOPBACK ]] || overall=FAIL
[[ $cua == FAIL ]] && overall=FAIL
print_status 'Overall' "$overall"
[[ $overall == PASS ]]
