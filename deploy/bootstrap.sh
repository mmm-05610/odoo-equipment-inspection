#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/common.sh"
umask 077
[[ "$(cat "$ROOT/.runtime/DEPLOYMENT_ID")" == inspection_public ]]
compose up -d db
# Compose waits for health when starting web; bootstrap accesses PostgreSQL first.
for attempt in $(seq 1 40); do compose exec -T db pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
if psql_admin -Atc "SELECT 1 FROM pg_database WHERE datname='inspection_template'" | grep -qx 1; then
  echo 'Template already exists; bootstrap will not overwrite it.' >&2; exit 1
fi
source "$ROOT/.runtime/secrets.env"
[[ "$ODOO_DB_PASSWORD" =~ ^[a-f0-9]{64}$ ]]
printf "CREATE ROLE odoo LOGIN PASSWORD '%s' NOSUPERUSER NOCREATEDB NOCREATEROLE;\n" "$ODOO_DB_PASSWORD" | psql_admin
compose exec -T db createdb -U postgres -O odoo inspection_template
compose run --rm --no-deps -T web odoo -d inspection_template -i inspection --without-demo=all --stop-after-init --max-cron-threads=0
# Mark as a protected clone source, no running Odoo process connects to it.
psql_admin -c 'ALTER DATABASE inspection_template ALLOW_CONNECTIONS false;'
echo 'Template ready. Run deploy/reset.sh to prepare the public demo.'
