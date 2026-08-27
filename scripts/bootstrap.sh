#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the historical root-level entrypoint as the implementation owner while
# providing the documented scripts/bootstrap.sh path.
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "$repo_root/bootstrap.sh" "$@"
