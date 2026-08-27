#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
hermes_home=${HERMES_HOME:-${HOME}/.hermes}
label=scheduled
destination=

usage() {
  cat >&2 <<'USAGE'
Usage: backup.sh [--label LABEL] [--destination DIRECTORY]

Creates a private, point-in-time Hermes/autonomy backup under
$HERMES_HOME/backups by default. The Chromium profile is intentionally never
copied.
USAGE
}

while (($#)); do
  case $1 in
    --label) [[ $# -ge 2 ]] || { usage; exit 2; }; label=$2; shift 2 ;;
    --destination) [[ $# -ge 2 ]] || { usage; exit 2; }; destination=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

[[ $label =~ ^[A-Za-z0-9._-]+$ ]] || {
  printf 'invalid backup label: %s\n' "$label" >&2
  exit 2
}
hermes_home=$(realpath -m -- "$hermes_home")
if [[ -z $destination ]]; then
  destination="$hermes_home/backups/${label}-$(date -u +%Y%m%dT%H%M%SZ)"
else
  destination=$(realpath -m -- "$destination")
fi
case $destination in
  "$hermes_home/backups"/*) ;;
  *) printf 'backup destination must be under %s/backups\n' "$hermes_home" >&2; exit 2 ;;
esac

install -d -m 0700 -- "$hermes_home/backups" "$destination"
copy_file() {
  local source=$1 target=$2
  [[ -f $source || -L $source ]] || return 0
  if [[ -L $source ]]; then
    install -d -m 0700 -- "$(dirname -- "$target")"
    ln -s -- "$(readlink -- "$source")" "$target"
  else
    install -D -m 0600 -- "$source" "$target"
  fi
}
copy_tree() {
  local source=$1 target=$2
  [[ -d $source ]] || return 0
  install -d -m 0700 -- "$target"
  cp -a -- "$source/." "$target/"
}
backup_sqlite() {
  local source=$1 target=$2
  [[ -f $source ]] || return 0
  install -d -m 0700 -- "$(dirname -- "$target")"
  python3 - "$source" "$target" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1:]
src = sqlite3.connect(source)
dst = sqlite3.connect(target)
try:
    src.backup(dst)
    dst.commit()
finally:
    dst.close()
    src.close()
PY
  chmod 0600 -- "$target"
}

printf 'Creating private backup: %s\n' "$destination"
copy_file "$hermes_home/config.yaml" "$destination/hermes/config.yaml"
copy_file "$hermes_home/.env" "$destination/hermes/.env"
copy_file "$hermes_home/auth.json" "$destination/hermes/auth.json"
copy_file "$hermes_home/gateway_state.json" "$destination/hermes/gateway_state.json"
copy_file "$hermes_home/.hermes_history" "$destination/hermes/.hermes_history"
copy_tree "$hermes_home/memories" "$destination/hermes/memories"
copy_tree "$hermes_home/sessions" "$destination/hermes/sessions"
copy_tree "$hermes_home/plugins" "$destination/hermes/plugins"
copy_tree "$hermes_home/skills" "$destination/hermes/skills"
backup_sqlite "$hermes_home/state.db" "$destination/hermes/state.db"
backup_sqlite "$hermes_home/cron/executions.db" "$destination/hermes/cron/executions.db"
backup_sqlite "$hermes_home/kanban.db" "$destination/hermes/kanban.db"

if [[ -f $hermes_home/vault/vault.db ]]; then
  backup_sqlite "$hermes_home/vault/vault.db" "$destination/vault/vault.db"
  for directory in journal facts projects decisions runbooks entities checkpoints archive state; do
    copy_tree "$hermes_home/vault/$directory" "$destination/vault/$directory"
  done
  copy_file "$hermes_home/vault/README.md" "$destination/vault/README.md"
fi

copy_file "$HOME/.config/systemd/user/hermes-gateway.service" "$destination/user-config/systemd/user/hermes-gateway.service"
copy_file "$HOME/.config/autostart/hermes-visible-chromium.desktop" "$destination/user-config/autostart/hermes-visible-chromium.desktop"
copy_file "$HOME/.config/hermes/graphical-session.env" "$destination/user-config/hermes/graphical-session.env"
for helper in hermes hermes-session-start hermes-graphical hermes-vault hermes-health hermes-mcp-probe codex chrome-devtools-mcp; do
  copy_file "$HOME/.local/bin/$helper" "$destination/user-config/local-bin/$helper"
done
copy_tree "$HOME/.local/share/hermes-autonomy/npm" \
  "$destination/user-config/local-share/hermes-autonomy/npm"

{
  printf '%s\n' 'Private Hermes autonomy backup; do not publish.'
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_hermes_home=%s\n' "$hermes_home"
  printf 'source_user=%s\n' "$(id -un)"
  printf '%s\n' 'chromium_profile=excluded'
  printf '%s\n' 'paths:'
  find "$destination" -mindepth 1 -printf '%m %u:%g %p\n' | sort
} > "$destination/MANIFEST.txt"
chmod 0600 -- "$destination/MANIFEST.txt"
printf 'BACKUP_DIR=%s\n' "$destination"
