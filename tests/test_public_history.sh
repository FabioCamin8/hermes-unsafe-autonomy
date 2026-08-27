#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
fixture_root=$(mktemp -d)
trap 'rm -rf -- "${fixture_root:-}"' EXIT

bash "$repo_root/scripts/validate-public-history.sh" >/dev/null

expect_rejection() {
  local fixture=$1
  if bash "$fixture/validate-public-history.sh" >/dev/null 2>&1; then
    printf 'history validator accepted invalid fixture: %s\n' "$fixture" >&2
    exit 1
  fi
}

make_bad_commit_fixture() {
  local fixture=$1
  git init -q "$fixture"
  printf '%s\n' 'fixture' >"$fixture/README.md"
  git -C "$fixture" add README.md
  git -C "$fixture" -c core.hooksPath=/dev/null \
    -c user.name='unexpected' -c user.email='unexpected@example.invalid' \
    commit -qm 'invalid commit identity'
  cp -- "$repo_root/scripts/validate-public-history.sh" "$fixture/validate-public-history.sh"
}

make_bad_tagger_fixture() {
  local fixture=$1
  git init -q "$fixture"
  git -C "$fixture" config user.name FabioCamin8
  git -C "$fixture" config user.email 278987681+FabioCamin8@users.noreply.github.com
  printf '%s\n' 'fixture' >"$fixture/README.md"
  git -C "$fixture" add README.md
  git -C "$fixture" commit -qm 'valid commit identity'
  git -C "$fixture" -c user.name='unexpected' -c user.email='unexpected@example.invalid' \
    tag -a v0.0.0 -m 'invalid tagger identity'
  cp -- "$repo_root/scripts/validate-public-history.sh" "$fixture/validate-public-history.sh"
}

make_lightweight_tag_fixture() {
  local fixture=$1
  git init -q "$fixture"
  git -C "$fixture" config user.name FabioCamin8
  git -C "$fixture" config user.email 278987681+FabioCamin8@users.noreply.github.com
  printf '%s\n' 'fixture' >"$fixture/README.md"
  git -C "$fixture" add README.md
  git -C "$fixture" commit -qm 'valid commit identity'
  git -C "$fixture" tag v0.0.0
  cp -- "$repo_root/scripts/validate-public-history.sh" "$fixture/validate-public-history.sh"
}

bad_commit="$fixture_root/bad-commit"
bad_tagger="$fixture_root/bad-tagger"
lightweight="$fixture_root/lightweight"
make_bad_commit_fixture "$bad_commit"
make_bad_tagger_fixture "$bad_tagger"
make_lightweight_tag_fixture "$lightweight"
expect_rejection "$bad_commit"
expect_rejection "$bad_tagger"
expect_rejection "$lightweight"
printf '%s\n' 'PUBLIC_HISTORY_TEST=PASS'
