#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_root"
scan_dir=$(mktemp -d)
trap 'rm -rf -- "${scan_dir:-}"' EXIT
status=0

allowed_name='FabioCamin8'
allowed_email='278987681+FabioCamin8@users.noreply.github.com'
commit_count=0
unexpected_author_count=0
unexpected_committer_count=0
unexpected_tagger_count=0
root_occurrences=0
domain_occurrences=0

for commit in $(git rev-list --all); do
  metadata=$(git show -s --format='%an%x09%ae%x09%cn%x09%ce' "$commit")
  IFS=$'\t' read -r author_name author_email committer_name committer_email <<<"$metadata"
  commit_count=$((commit_count + 1))

  if [[ $author_name != "$allowed_name" || $author_email != "$allowed_email" ]]; then
    unexpected_author_count=$((unexpected_author_count + 1))
    printf 'HISTORY_UNEXPECTED_AUTHOR commit=%s\n' "$commit" >&2
    status=1
  fi
  if [[ $committer_name != "$allowed_name" || $committer_email != "$allowed_email" ]]; then
    unexpected_committer_count=$((unexpected_committer_count + 1))
    printf 'HISTORY_UNEXPECTED_COMMITTER commit=%s\n' "$commit" >&2
    status=1
  fi
  for field in "$author_name" "$author_email" "$committer_name" "$committer_email"; do
    case "$field" in
      *root*) root_occurrences=$((root_occurrences + 1)) ;;
      *agent.caminotto.it*) domain_occurrences=$((domain_occurrences + 1)) ;;
    esac
  done
done

tag_count=0
while IFS=$'\t' read -r ref object_type tagger_name tagger_email; do
  tag_count=$((tag_count + 1))
  tagger_email=${tagger_email#<}
  tagger_email=${tagger_email%>}
  if [[ $object_type != tag ]]; then
    unexpected_tagger_count=$((unexpected_tagger_count + 1))
    printf 'HISTORY_TAG_NOT_ANNOTATED ref=%s\n' "$ref" >&2
    status=1
    continue
  fi
  if [[ $tagger_name != "$allowed_name" || $tagger_email != "$allowed_email" ]]; then
    unexpected_tagger_count=$((unexpected_tagger_count + 1))
    printf 'HISTORY_UNEXPECTED_TAGGER ref=%s\n' "$ref" >&2
    status=1
  fi
  for field in "$tagger_name" "$tagger_email"; do
    case "$field" in
      *root*) root_occurrences=$((root_occurrences + 1)) ;;
      *agent.caminotto.it*) domain_occurrences=$((domain_occurrences + 1)) ;;
    esac
  done
done < <(git for-each-ref refs/tags --format='%(refname)%09%(objecttype)%09%(taggername)%09%(taggeremail)')

printf 'IDENTITY_SCAN commits=%s tags=%s unexpected_authors=%s unexpected_committers=%s unexpected_taggers=%s root_occurrences=%s agent_caminotto_occurrences=%s\n' \
  "$commit_count" "$tag_count" "$unexpected_author_count" "$unexpected_committer_count" "$unexpected_tagger_count" "$root_occurrences" "$domain_occurrences"

for commit in $(git rev-list --all); do
  while IFS= read -r path; do
    case "$path" in
      .env|.env.*|*/.env|*/.env.*|auth.json|*/auth.json|*.db|*.db-wal|*.db-shm|*.sqlite*|*.jsonl|*.pem|*.key|*.token|*.secret|*.credentials|*.cookie|browser-profile/*|*/browser-profile/*|chromium-profile/*|*/chromium-profile/*|sessions/*|*/sessions/*|backups/*|*/backups/*)
        printf 'HISTORY_FORBIDDEN_PATH commit=%s path=%s\n' "$commit" "$path" >&2
        status=1
        ;;
    esac
  done < <(git ls-tree -r --name-only "$commit")

  if git grep -I -q -E -e '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|(^|[^A-Za-z0-9_])(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{16,}|AIza[A-Za-z0-9_-]{24,})([^A-Za-z0-9_]|$)' "$commit" -- . ':!tests/test_vault.py'; then
    printf 'HISTORY_SECRET_PATTERN commit=%s\n' "$commit" >&2
    status=1
  fi
  if git grep -I -q -E -e '(^|[^A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}([^A-Za-z0-9_]|$)' "$commit" -- . ':!tests/test_vault.py'; then
    printf 'HISTORY_NON_SYNTHETIC_TOKEN commit=%s\n' "$commit" >&2
    status=1
  fi
  test_hits="$scan_dir/test-hits"
  if git grep -I -n -E -e '(^|[^A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}([^A-Za-z0-9_]|$)' "$commit" -- tests/test_vault.py >"$test_hits" 2>/dev/null; then
    if rg -q -v 'sk-test-' "$test_hits"; then
      printf 'HISTORY_NON_SYNTHETIC_TEST_TOKEN commit=%s\n' "$commit" >&2
      status=1
    fi
  fi
  if git grep -I -q -E -e '192\.168\.|(^|[^0-9])10\.[0-9]{1,3}(\.[0-9]{1,3}){2}([^0-9]|$)|(^|[^0-9])172\.(1[6-9]|2[0-9]|3[0-1])\.' "$commit" -- .; then
    printf 'HISTORY_PRIVATE_NETWORK_LITERAL commit=%s\n' "$commit" >&2
    status=1
  fi
done

archive="$scan_dir/release.tar"
git archive --format=tar HEAD >"$archive"
if tar -tf "$archive" | rg -qi '(^|/)(\.env|auth\.json|.*\.(db|db-wal|db-shm|sqlite|jsonl|pem|key|token|secret|credentials|cookie))$|(^|/)(browser-profile|chromium-profile|sessions|backups)(/|$)'; then
  printf '%s\n' 'ARCHIVE_FORBIDDEN_PATH' >&2
  status=1
fi

if ((status == 0)); then
  printf '%s\n' 'HISTORY_SCAN=PASS'
else
  printf '%s\n' 'HISTORY_SCAN=FAIL' >&2
fi
exit "$status"
