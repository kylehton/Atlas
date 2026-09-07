#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

for command_name in cloudflared curl docker uv; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done

docker info >/dev/null 2>&1 || {
  echo "Docker is not running." >&2
  exit 1
}

exec uv run --locked python scripts/telegram_live_smoke.py
