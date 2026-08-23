#!/usr/bin/env bash
set -uo pipefail

suite=$1
shift

set +e
uv run --locked pytest "$suite" "$@"
status=$?
set -e

if [[ $status -eq 5 ]]; then
  echo "No tests exist in $suite yet."
  exit 0
fi

exit "$status"

