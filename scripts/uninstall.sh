#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
hermes_home=${HERMES_HOME:-${HOME}/.hermes}
yes=false
remove_root=false
remove_mcp=false
remove_timer=false
plugin_dir="$hermes_home/plugins/hermes_vault"

usage() {
  cat >&2 <<'USAGE'
Usage: uninstall.sh --yes [--remove-mcp] [--remove-timer] [--remove-unsafe-root]

Removes only this project's provider/CLI and explicitly selected integrations.
It never removes sessions or browser profiles. Use rollback.sh separately to
restore a saved Hermes configuration.
USAGE
}

while (($#)); do
  case $1 in
    --yes) yes=true; shift ;;
    --remove-mcp) remove_mcp=true; shift ;;
    --remove-timer) remove_timer=true; shift ;;
    --remove-unsafe-root) remove_root=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done
[[ $yes == true ]] || { printf '%s\n' 'uninstall requires --yes' >&2; exit 2; }
[[ $plugin_dir == "$hermes_home/plugins/hermes_vault" ]] || exit 2

if [[ -d $plugin_dir ]]; then
  rm -rf -- "$plugin_dir"
  printf 'Removed project provider: %s\n' "$plugin_dir"
fi
for helper in hermes-vault hermes-health hermes-mcp-probe; do
  if [[ -f $HOME/.local/bin/$helper || -L $HOME/.local/bin/$helper ]]; then
    rm -f -- "$HOME/.local/bin/$helper"
  fi
done

if [[ $remove_timer == true ]]; then
  for unit in hermes-vault-maintenance.service hermes-vault-maintenance.timer; do
    systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
    rm -f -- "$HOME/.config/systemd/user/$unit"
  done
  systemctl --user daemon-reload
fi

if [[ $remove_mcp == true ]]; then
  for name in chrome-devtools codex; do
    if command -v hermes >/dev/null 2>&1; then
      printf 'y\n' | hermes mcp remove "$name" >/dev/null 2>&1 || true
    fi
  done
fi

if [[ $remove_root == true ]]; then
  [[ $EUID -eq 0 ]] || { printf '%s\n' '--remove-unsafe-root requires root' >&2; exit 2; }
  root_rule=/etc/sudoers.d/hermes-unsafe-autonomy
  if [[ -f $root_rule ]]; then
    rm -f -- "$root_rule"
    visudo -cf /etc/sudoers
  fi
fi
printf '%s\n' 'Project-owned autonomy files removed; unrelated Hermes state was preserved.'
