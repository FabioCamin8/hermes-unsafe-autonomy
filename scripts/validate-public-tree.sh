#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_root"
status=0
while IFS= read -r -d '' path; do
  case $path in
    .env|.env.*|*.db|*.db-wal|*.db-shm|vault/*|backups/*|browser-profile/*|chromium-profile/*|*.pem|*.key|*.token|*.secret|*.credentials|*.cookie|*.session)
      printf 'FORBIDDEN_RUNTIME_PATH %s\n' "$path" >&2
      status=1
      ;;
  esac
done < <(git ls-files -co --exclude-standard -z)

secret_scan_rc=0
rg -n --regexp '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{24,})\b' \
  --hidden --glob '!.git/**' --glob '!tests/**' --glob '!*.example*' . || secret_scan_rc=$?
if ((secret_scan_rc == 0)); then
  printf '%s\n' 'OBVIOUS_SECRET_PATTERN_FOUND' >&2
  status=1
elif ((secret_scan_rc > 1)); then
  printf 'secret scan failed with exit code %s\n' "$secret_scan_rc" >&2
  status=1
fi

if git ls-files -co --exclude-standard | rg -i '(^|/)(cookies?|auth|credentials?|browser-profile|chromium-profile)(/|\.|$)'; then
  printf '%s\n' 'AUTH_RUNTIME_MATERIAL_FOUND' >&2
  status=1
fi

if ((status == 0)); then
  printf '%s\n' 'PUBLIC_TREE_SCAN=PASS'
else
  printf '%s\n' 'PUBLIC_TREE_SCAN=FAIL'
fi
exit "$status"
