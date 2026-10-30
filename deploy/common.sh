#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export ROOT
compose() { timeout 600 docker compose -p inspection_public --project-directory "$ROOT" --env-file "$ROOT/.runtime/secrets.env" -f "$ROOT/deploy/compose.yaml" "$@"; }
psql_admin() { compose exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 "$@"; }
