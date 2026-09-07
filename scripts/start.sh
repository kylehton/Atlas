#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

for command_name in curl docker; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done

docker info >/dev/null 2>&1 || {
  echo "Docker is not running." >&2
  exit 1
}

docker compose build api
docker compose up --detach --wait postgres
docker compose run --rm api alembic upgrade head
docker compose up --detach api scheduler caddy

published_address=$(docker compose port caddy 80)
local_port=${published_address##*:}

for _ in {1..30}; do
  if curl --fail --silent "http://127.0.0.1:$local_port/health/ready" >/dev/null; then
    echo "Atlas is ready at http://127.0.0.1:$local_port"
    echo "Stop it with: atlas run stop"
    exit 0
  fi
  sleep 1
done

docker compose logs api caddy >&2
echo "Atlas did not become ready within 30 seconds." >&2
exit 1
