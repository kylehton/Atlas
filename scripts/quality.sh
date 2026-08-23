#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

case "${1:-check}" in
  check)
    uv run --locked ruff check .
    uv run --locked ruff format --check .
    uv run --locked rumdl check .
    ;;
  fix)
    uv run --locked ruff check --fix .
    uv run --locked ruff format .
    uv run --locked rumdl fmt .
    uv run --locked rumdl check .
    ;;
  *)
    echo "Usage: $0 [check|fix]" >&2
    exit 2
    ;;
esac
