#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir" && pwd)
hermes_user=${HERMES_USER:-hermes}
chrome_mcp_version=${CHROME_DEVTOOLS_MCP_VERSION:-1.8.0}
codex_version=${CODEX_VERSION:-0.150.1}
cdp_port=${HERMES_CDP_PORT:-9222}
unsafe_root=false

usage() {
  cat >&2 <<'USAGE'
Usage: bootstrap.sh --enable-unsafe-root

Install the Hermes Vault provider, pinned local MCP binaries, user timer, and
the intentionally unsafe passwordless-root sudo rule on the dedicated VM.

This script must be run as root from the transferred repository. It never
performs browser login or Codex/OAuth authentication. Existing secrets and the
Chromium profile remain in the private Hermes home and are never copied here.
USAGE
}

die() {
  printf 'bootstrap: %s\n' "$*" >&2
  exit 1
}

sudoers_tmp=
plugin_stage=
cleanup() {
  if [[ -n $sudoers_tmp && -e $sudoers_tmp ]]; then
    rm -f -- "$sudoers_tmp"
  fi
  if [[ -n $plugin_stage && -d $plugin_stage ]]; then
    rm -rf -- "$plugin_stage"
  fi
}
trap cleanup EXIT

while (($#)); do
  case $1 in
    --enable-unsafe-root) unsafe_root=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || die 'run as root'
[[ $unsafe_root == true ]] || die 'refusing to install passwordless root without --enable-unsafe-root'
[[ -d $repo_root/plugin/hermes_vault ]] || die "missing provider source under $repo_root"
getent passwd "$hermes_user" >/dev/null || die "user not found: $hermes_user"
hermes_uid=$(id -u "$hermes_user")
hermes_gid=$(id -g "$hermes_user")
hermes_user_home=$(getent passwd "$hermes_user" | cut -d: -f6)
[[ -n $hermes_user_home && -d $hermes_user_home ]] || die "Hermes user home not found: $hermes_user_home"
hermes_home=${HERMES_HOME:-$hermes_user_home/.hermes}
hermes_bin=${HERMES_BIN:-$hermes_user_home/.local/bin/hermes}
[[ -x $hermes_bin ]] || die "Hermes executable not found: $hermes_bin"
[[ $cdp_port =~ ^[0-9]+$ ]] || die "invalid HERMES_CDP_PORT: $cdp_port"
((10#$cdp_port >= 1 && 10#$cdp_port <= 65535)) || die "invalid HERMES_CDP_PORT: $cdp_port"
[[ -d $hermes_home ]] || die "Hermes home not found: $hermes_home"
hermes_home=$(realpath -e -- "$hermes_home")
[[ $(stat -c %U "$hermes_home") == "$hermes_user" ]] || die "Hermes home is not owned by $hermes_user"

gateway_unit="$hermes_user_home/.config/systemd/user/hermes-gateway.service"
[[ -f $gateway_unit ]] || die "user gateway unit not found: $gateway_unit"
rg -q 'ExecStart=.*gateway run' "$gateway_unit" || die 'gateway unit is not a Hermes user gateway'
[[ ! -e /etc/systemd/system/hermes-gateway.service ]] || die 'system-level Hermes gateway unit exists; refusing mixed privilege boundary'

printf 'Target user: %s (uid=%s)\n' "$hermes_user" "$hermes_uid"
printf 'Hermes home: %s\n' "$hermes_home"
printf 'Repository: %s\n' "$repo_root"
cd -- "$repo_root"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl iproute2 python3 sqlite3 sudo
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends nodejs npm
fi

command -v visudo >/dev/null 2>&1 || die 'visudo was not installed'
command -v ss >/dev/null 2>&1 || die 'ss was not installed'
command -v curl >/dev/null 2>&1 || die 'curl was not installed'
command -v npm >/dev/null 2>&1 || die 'npm was not installed'

runtime_dir="/run/user/$hermes_uid"
as_hermes() {
  sudo -n -u "$hermes_user" -H env \
    HOME="$hermes_user_home" \
    HERMES_HOME="$hermes_home" \
    PATH="$hermes_user_home/.local/bin:$hermes_home/node/bin:$hermes_home/hermes-agent/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
    "$@"
}

node_version=$(as_hermes node --version)
[[ $node_version =~ ^v([0-9]+)\. ]] || die "could not parse Hermes Node version: $node_version"
node_major=${BASH_REMATCH[1]}
(( node_major >= 20 )) || die "Chrome DevTools MCP requires Node 20 or newer; found $node_version"
as_hermes npm --version >/dev/null || die 'npm is not usable by the Hermes user'

as_hermes systemctl --user show-environment >/dev/null 2>&1 || \
  die "cannot reach the $hermes_user user systemd manager"

backup_output=$(as_hermes "$script_dir/scripts/backup.sh" --label pre-autonomy)
printf '%s\n' "$backup_output"
backup_dir=$(printf '%s\n' "$backup_output" | sed -n 's/^BACKUP_DIR=//p' | tail -n 1)
[[ -n $backup_dir && -d $backup_dir ]] || die 'pre-autonomy backup did not produce a directory'

sudoers_rule=/etc/sudoers.d/hermes-unsafe-autonomy
sudoers_tmp=$(mktemp /etc/sudoers.d/.hermes-unsafe-autonomy.XXXXXX)
printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$hermes_user" > "$sudoers_tmp"
chown root:root "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
install -o root -g root -m 0440 -- "$sudoers_tmp" "$sudoers_rule"
rm -f -- "$sudoers_tmp"
sudoers_tmp=
visudo -cf /etc/sudoers
passwordless_root_uid=$(as_hermes sudo -n id -u)
[[ $passwordless_root_uid == 0 ]] || die 'hermes cannot execute passwordless sudo as root'
printf '%s\n' 'PASSWORDLESS_ROOT=PASS'

plugin_root="$hermes_home/plugins"
plugin_dir="$plugin_root/hermes_vault"
install -d -o "$hermes_user" -g "$hermes_user" -m 0700 -- "$plugin_root"
plugin_stage=$(mktemp -d "$plugin_root/.hermes_vault.stage.XXXXXX")
chown "$hermes_user:$hermes_user" "$plugin_stage"
cp -a -- "$repo_root/plugin/hermes_vault/." "$plugin_stage/"
chown -R "$hermes_user:$hermes_user" "$plugin_stage"
find "$plugin_stage" -type d -exec chmod 0700 {} +
find "$plugin_stage" -type f -exec chmod 0600 {} +
plugin_previous=
if [[ -e $plugin_dir || -L $plugin_dir ]]; then
  plugin_previous="$plugin_root/.hermes_vault.previous.$BASHPID"
  mv -- "$plugin_dir" "$plugin_previous"
fi
mv -- "$plugin_stage" "$plugin_dir"
plugin_stage=
if [[ -n $plugin_previous ]]; then
  rm -rf -- "$plugin_previous"
fi

local_bin="$hermes_user_home/.local/bin"
install -d -o "$hermes_user" -g "$hermes_user" -m 0700 -- "$local_bin"
install -o "$hermes_user" -g "$hermes_user" -m 0700 -- \
  "$repo_root/bin/hermes-vault" "$local_bin/hermes-vault"
install -o "$hermes_user" -g "$hermes_user" -m 0700 -- \
  "$repo_root/scripts/health.sh" "$local_bin/hermes-health"
install -o "$hermes_user" -g "$hermes_user" -m 0700 -- \
  "$repo_root/scripts/mcp_probe.py" "$local_bin/hermes-mcp-probe"

systemd_user_dir="$hermes_user_home/.config/systemd/user"
install -d -o "$hermes_user" -g "$hermes_user" -m 0700 -- "$systemd_user_dir"
install -o "$hermes_user" -g "$hermes_user" -m 0644 -- \
  "$repo_root/scripts/systemd/hermes-vault-maintenance.service" \
  "$systemd_user_dir/hermes-vault-maintenance.service"
install -o "$hermes_user" -g "$hermes_user" -m 0644 -- \
  "$repo_root/scripts/systemd/hermes-vault-maintenance.timer" \
  "$systemd_user_dir/hermes-vault-maintenance.timer"

autonomy_root="$hermes_user_home/.local/share/hermes-autonomy"
install -d -o "$hermes_user" -g "$hermes_user" -m 0700 -- "$autonomy_root"
npm_prefix="$autonomy_root/npm"
install -d -o "$hermes_user" -g "$hermes_user" -m 0700 -- "$npm_prefix"
[[ $chrome_mcp_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid Chrome MCP version: $chrome_mcp_version"
[[ $codex_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid Codex version: $codex_version"
as_hermes npm install --prefix "$npm_prefix" --no-save --package-lock=false \
  --no-fund --no-audit --ignore-scripts \
  "chrome-devtools-mcp@$chrome_mcp_version" \
  "@openai/codex@$codex_version"
as_hermes npm list --prefix "$npm_prefix" --depth=0 \
  "chrome-devtools-mcp@$chrome_mcp_version" "@openai/codex@$codex_version" >/dev/null

for executable in chrome-devtools-mcp codex; do
  source_bin="$npm_prefix/node_modules/.bin/$executable"
  target_bin="$local_bin/$executable"
  [[ -x $source_bin ]] || die "npm did not install executable: $source_bin"
  rm -f -- "$target_bin"
  ln -s -- "$source_bin" "$target_bin"
  chown -h "$hermes_user:$hermes_user" "$target_bin"
done
[[ -x $local_bin/hermes-graphical ]] || die 'CUA fallback helper hermes-graphical is missing'

config_value() {
  as_hermes "$hermes_bin" config get "$1" 2>/dev/null || true
}

current_provider=$(config_value memory.provider)
if [[ -n $current_provider && $current_provider != hermes_vault ]]; then
  die "another memory provider is active: $current_provider"
fi

assert_mcp_slot() {
  local name=$1 expected_command=$2
  local existing_command existing_url
  existing_command=$(config_value "mcp_servers.$name.command")
  existing_url=$(config_value "mcp_servers.$name.url")
  [[ -z $existing_url ]] || die "MCP server '$name' already uses a URL; remove it explicitly before bootstrap"
  [[ -z $existing_command || $existing_command == "$expected_command" ]] || \
    die "MCP server '$name' already points to another command: $existing_command"
}

chrome_bin="$local_bin/chrome-devtools-mcp"
codex_bin="$local_bin/codex"
assert_mcp_slot chrome-devtools "$chrome_bin"
assert_mcp_slot codex "$codex_bin"

set_config() {
  as_hermes "$hermes_bin" config set --force "$1" "$2" >/dev/null
}

set_config memory.memory_enabled true
set_config memory.provider hermes_vault
set_config compression.enabled true
set_config compression.checkpoint_required true
set_config browser.backend off
set_config browser.cdp_url "http://127.0.0.1:$cdp_port"
set_config computer_use.grant_existing_profile true
set_config computer_use.cua_telemetry false
set_config mcp_servers.chrome-devtools.command "$chrome_bin"
set_config mcp_servers.chrome-devtools.args "[\"--browser-url=http://127.0.0.1:$cdp_port\",\"--no-usage-statistics\",\"--no-performance-crux\"]"
set_config mcp_servers.chrome-devtools.enabled true
set_config mcp_servers.codex.command "$codex_bin"
set_config mcp_servers.codex.args '["mcp-server"]'
set_config mcp_servers.codex.enabled true

cdp_url="http://127.0.0.1:$cdp_port"
curl -fsS "$cdp_url/json/version" >/dev/null || \
  die "Chrome CDP is not reachable on loopback 127.0.0.1:$cdp_port"
if ! ss -ltnH | awk -v expected="127.0.0.1:$cdp_port" '$4 == expected { found = 1 } END { exit !found }'; then
  die "Chrome CDP is not loopback-only on 127.0.0.1:$cdp_port"
fi

as_hermes systemctl --user daemon-reload
as_hermes systemctl --user enable --now hermes-vault-maintenance.timer
as_hermes systemctl --user restart hermes-gateway.service
as_hermes systemctl --user is-active --quiet hermes-gateway.service || \
  die 'Hermes gateway did not remain active after configuration'

as_hermes "$local_bin/hermes-vault" integrity --json
as_hermes "$hermes_bin" memory status
as_hermes "$hermes_bin" mcp list
as_hermes env CHROME_DEVTOOLS_MCP_COMMAND="$chrome_bin" \
  HERMES_CDP_PORT="$cdp_port" \
  "$repo_root/scripts/validate-chrome-mcp.sh"

if as_hermes python3 "$repo_root/scripts/mcp_probe.py" "$codex_bin" --timeout 60 -- mcp-server; then
  printf '%s\n' 'CODEX_MCP_PROBE=PASS'
else
  printf '%s\n' 'CODEX_MCP_PROBE=UNVERIFIED_AUTH_MAY_BE_REQUIRED'
fi

as_hermes "$hermes_bin" config get memory.provider
as_hermes "$hermes_bin" config get compression.checkpoint_required
as_hermes "$local_bin/hermes-health"
printf 'PRE_AUTONOMY_BACKUP=%s\n' "$backup_dir"
printf 'CHROME_DEVTOOLS_MCP_VERSION=%s\n' "$chrome_mcp_version"
printf 'CODEX_VERSION=%s\n' "$codex_version"
printf 'HERMES_CDP_PORT=%s\n' "$cdp_port"
printf '%s\n' 'BOOTSTRAP=PASS'
