#!/usr/bin/env bash
# No caller-supplied paths, database names, shell fragments or options.
set -Eeuo pipefail
[[ $# == 0 ]] || { echo 'This command accepts no arguments.' >&2; exit 2; }
# shellcheck source=deploy/common.sh
source "$(dirname -- "$0")/common.sh"
umask 077
[[ "$(cat "$ROOT/.runtime/DEPLOYMENT_ID")" == inspection_public ]]
exec 9>"$ROOT/.runtime/reset.lock"
flock -n 9 || { echo 'A reset is already running.' >&2; exit 3; }
# Generated root-owned values, deliberately absent from source control.
# shellcheck disable=SC1091
source "$ROOT/.runtime/secrets.env"
old_renamed=0
new_renamed=0
files_switched=0
files() { compose run --rm --no-deps -T --entrypoint python3 web - "$1" < "$ROOT/deploy/filestore.py"; }
rollback() {
  result=$?
  trap - ERR
  if [[ $old_renamed == 1 || $new_renamed == 1 ]]; then
    echo 'PHASE Restoring previous demonstration'
    compose stop web || true
    if [[ $new_renamed == 1 ]]; then psql_admin -c 'DROP DATABASE IF EXISTS inspection_demo WITH (FORCE);' || true; fi
    if [[ $old_renamed == 1 ]]; then
      psql_admin -c 'ALTER DATABASE inspection_previous RENAME TO inspection_demo;' || true
      psql_admin -c 'ALTER DATABASE inspection_demo ALLOW_CONNECTIONS true;' || true
    fi
    if [[ $files_switched == 1 ]]; then files rollback || true; fi
    compose up -d web || true
  fi
  exit "$result"
}
trap rollback ERR
echo 'PHASE Preparing a clean database'
psql_admin -c 'DROP DATABASE IF EXISTS inspection_next WITH (FORCE);'
compose exec -T db createdb -U postgres -O odoo -T inspection_template inspection_next
files prepare
echo 'PHASE Loading factory data and today-based schedules'
compose run --rm --no-deps -T web odoo shell -d inspection_next --no-http --max-cron-threads=0 < "$ROOT/deploy/seed_public.py"
echo 'PHASE Switching the demonstration'
compose stop web
psql_admin -c 'DROP DATABASE IF EXISTS inspection_previous WITH (FORCE);'
if psql_admin -Atc "SELECT 1 FROM pg_database WHERE datname='inspection_demo'" | grep -qx 1; then
  psql_admin -c 'ALTER DATABASE inspection_demo RENAME TO inspection_previous;'
  old_renamed=1
  psql_admin -c 'ALTER DATABASE inspection_previous ALLOW_CONNECTIONS false;'
fi
psql_admin -c 'ALTER DATABASE inspection_next RENAME TO inspection_demo;'
new_renamed=1
files_switched=1
files switch
compose up -d web
echo 'PHASE Checking the restored service'
ready=0
for _ in $(seq 1 50); do
  if curl --noproxy '*' --fail --silent --max-time 4 "http://${ODOO_BIND_IP}:8076/web/login" >/dev/null; then ready=1; break; fi
  sleep 2
done
[[ $ready == 1 ]]
trap - ERR
echo 'RESET_COMPLETE'
