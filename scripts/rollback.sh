#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-${HOME}/.hermes}
backup_dir=
restart=false

usage() {
  cat >&2 <<'USAGE'
Usage: rollback.sh --backup-dir DIRECTORY [--restart]

Restores the Hermes configuration and project-owned runtime metadata from a
private backup under $HERMES_HOME/backups. The current state is backed up
first. It does not delete sessions, browser profiles, or unrelated MCP/skill
state.
USAGE
}

while (($#)); do
  case $1 in
    --backup-dir) [[ $# -ge 2 ]] || { usage; exit 2; }; backup_dir=$2; shift 2 ;;
    --restart) restart=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done
[[ -n $backup_dir ]] || { usage; exit 2; }
hermes_home=$(realpath -m -- "$hermes_home")
backup_dir=$(realpath -e -- "$backup_dir")
case $backup_dir in
  "$hermes_home/backups"/*) ;;
  *) printf 'refusing backup outside %s/backups\n' "$hermes_home" >&2; exit 2 ;;
esac
[[ -f $backup_dir/hermes/config.yaml ]] || {
  printf 'backup has no Hermes config: %s\n' "$backup_dir" >&2
  exit 1
}

"$script_dir/backup.sh" --label pre-rollback
copy_file() {
  local source=$1 target=$2 mode=$3
  if [[ -L $source ]]; then
    install -d -m 0700 -- "$(dirname -- "$target")"
    rm -f -- "$target"
    ln -s -- "$(readlink -- "$source")" "$target"
  elif [[ -f $source ]]; then
    install -D -m "$mode" -- "$source" "$target"
  fi
}
replace_tree() {
  local source=$1 target=$2
  [[ -d $source ]] || return 0
  [[ -w $(dirname -- "$target") ]] || {
    printf 'rollback target parent is not writable: %s\n' "$(dirname -- "$target")" >&2
    exit 1
  }
  local stage="${target}.rollback-stage.$$"
  rm -rf -- "$stage"
  cp -a -- "$source" "$stage"
  rm -rf -- "$target"
  mv -- "$stage" "$target"
}
remove_if_missing() {
  local source=$1 target=$2
  [[ -e $source || -L $source ]] || rm -f -- "$target"
}
copy_file "$backup_dir/hermes/config.yaml" "$hermes_home/config.yaml" 0600
copy_file "$backup_dir/hermes/.env" "$hermes_home/.env" 0600
copy_file "$backup_dir/hermes/auth.json" "$hermes_home/auth.json" 0600
copy_file "$backup_dir/hermes/gateway_state.json" "$hermes_home/gateway_state.json" 0600
copy_file "$backup_dir/hermes/.hermes_history" "$hermes_home/.hermes_history" 0600
replace_tree "$backup_dir/hermes/plugins/hermes_vault" "$hermes_home/plugins/hermes_vault"
replace_tree "$backup_dir/vault" "$hermes_home/vault"
runtime_backup="$backup_dir/user-config/local-share/hermes-autonomy/npm"
runtime_target="$HOME/.local/share/hermes-autonomy/npm"
if [[ -d $runtime_backup ]]; then
  replace_tree "$runtime_backup" "$runtime_target"
else
  rm -rf -- "$runtime_target"
fi
copy_file "$backup_dir/user-config/systemd/user/hermes-gateway.service" "$HOME/.config/systemd/user/hermes-gateway.service" 0600
copy_file "$backup_dir/user-config/autostart/hermes-visible-chromium.desktop" "$HOME/.config/autostart/hermes-visible-chromium.desktop" 0600
copy_file "$backup_dir/user-config/hermes/graphical-session.env" "$HOME/.config/hermes/graphical-session.env" 0600
for helper in hermes hermes-session-start hermes-graphical hermes-vault hermes-health hermes-mcp-probe codex chrome-devtools-mcp; do
  copy_file "$backup_dir/user-config/local-bin/$helper" "$HOME/.local/bin/$helper" 0700
  case $helper in
    hermes-vault|hermes-health|hermes-mcp-probe|codex|chrome-devtools-mcp)
      remove_if_missing "$backup_dir/user-config/local-bin/$helper" "$HOME/.local/bin/$helper"
      ;;
  esac
done

if [[ $restart == true ]]; then
  export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
  systemctl --user daemon-reload
  systemctl --user restart hermes-gateway.service
fi
printf 'Rollback restored config from %s\n' "$backup_dir"
printf 'Gateway restarted: %s\n' "$restart"
