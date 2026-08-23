#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

for command_name in curl docker uv; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done

./scripts/quality.sh fix
./scripts/quality.sh check
./scripts/test.sh

project_name=atlas-build-check
export ATLAS_HTTP_PORT=${ATLAS_BUILD_HTTP_PORT:-18080}

cleanup() {
  docker compose --project-name "$project_name" down --volumes --remove-orphans \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
docker compose --project-name "$project_name" build api
docker compose --project-name "$project_name" up --detach postgres
docker compose --project-name "$project_name" run --rm api alembic upgrade head
docker compose --project-name "$project_name" run --rm --no-deps worker \
  python -c "from atlas.worker import run; assert callable(run)"
docker compose --project-name "$project_name" run --rm --no-deps scheduler \
  python -c "from atlas.scheduler import run; assert callable(run)"
docker compose --project-name "$project_name" up --detach api scheduler caddy

for _ in {1..30}; do
  if curl --fail --silent "http://127.0.0.1:$ATLAS_HTTP_PORT/openapi.json" >/dev/null; then
    echo "Atlas container build and smoke check passed."
    exit 0
  fi
  sleep 1
done

docker compose --project-name "$project_name" logs api caddy >&2
echo "Atlas API did not become ready within 30 seconds." >&2
exit 1
