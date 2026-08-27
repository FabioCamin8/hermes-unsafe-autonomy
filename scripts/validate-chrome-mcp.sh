#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
command -v curl >/dev/null 2>&1 || { printf '%s\n' 'curl is required' >&2; exit 1; }
command -v ss >/dev/null 2>&1 || { printf '%s\n' 'ss is required' >&2; exit 1; }
chrome_command=${CHROME_DEVTOOLS_MCP_COMMAND:-"$HOME/.local/bin/chrome-devtools-mcp"}
cdp_port=${HERMES_CDP_PORT:-9222}
[[ $cdp_port =~ ^[0-9]+$ ]] && ((10#$cdp_port >= 1 && 10#$cdp_port <= 65535)) \
  || { printf 'invalid HERMES_CDP_PORT: %s\n' "$cdp_port" >&2; exit 1; }
cdp_url="http://127.0.0.1:$cdp_port"
[[ -x $chrome_command ]] || { printf 'missing Chrome MCP executable: %s\n' "$chrome_command" >&2; exit 1; }
curl -fsS "$cdp_url/json/version" >/dev/null
curl -fsS "$cdp_url/json/list" >/dev/null
listening=$(ss -ltnH | awk -v port=":$cdp_port" '$4 ~ port "$" {print $4}' || true)
[[ -n $listening ]] || { printf 'CDP port %s is not listening\n' "$cdp_port" >&2; exit 1; }
if ! ss -ltnH | awk -v expected="127.0.0.1:$cdp_port" '$4 == expected { found = 1 } END { exit !found }'; then
  printf '%s\n' 'CDP is not loopback-only' >&2
  exit 1
fi
python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/mcp_probe.py" \
  "$chrome_command" \
  --exercise-browser \
  -- \
  "--browser-url=$cdp_url" \
  --no-usage-statistics \
  --no-performance-crux
