#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

project_name=atlas-integration-test
test_database_url=${ATLAS_TEST_DATABASE_URL:-}
owns_postgres=0

cleanup() {
  if [[ $owns_postgres == 1 ]]; then
    docker compose --project-name "$project_name" --file docker-compose.test.yml down --volumes \
      >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ -z $test_database_url ]]; then
  command -v docker >/dev/null || {
    echo "Docker is required for integration tests when ATLAS_TEST_DATABASE_URL is unset." >&2
    exit 1
  }
  owns_postgres=1
  cleanup
  docker compose --project-name "$project_name" --file docker-compose.test.yml \
    up --detach --wait postgres
  published_address=$(docker compose --project-name "$project_name" \
    --file docker-compose.test.yml port postgres 5432)
  test_port=${published_address##*:}
  test_database_url="postgresql+psycopg://atlas:atlas@127.0.0.1:$test_port/atlas_test"
fi

export ATLAS_ENVIRONMENT=test
export ATLAS_DATABASE_URL=$test_database_url
export ATLAS_TEST_DATABASE_URL=$test_database_url

uv run --locked alembic upgrade head
./scripts/_pytest.sh tests/integration "$@"
