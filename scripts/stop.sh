#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

command -v docker >/dev/null || {
  echo "Missing required command: docker" >&2
  exit 1
}

docker compose down --remove-orphans
