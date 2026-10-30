#!/usr/bin/env python3
"""Generate local deployment secrets, never print them or commit them."""
import os,secrets,sys
from pathlib import Path
from urllib.parse import urlparse
root=Path(__file__).resolve().parent.parent
origin=sys.argv[1].rstrip('/')
assert urlparse(origin).scheme=='https' and urlparse(origin).hostname and '\n' not in origin
runtime=root/'.runtime';runtime.mkdir(exist_ok=True);runtime.chmod(0o700)
path=runtime/'secrets.env'
if path.exists():
    raise SystemExit('Deployment is already configured; existing secrets were not overwritten.')
values={'PG_ADMIN_PASSWORD':secrets.token_hex(32),'ODOO_DB_PASSWORD':secrets.token_hex(32),'DEMO_ORIGIN':origin,'ODOO_BIND_IP':'172.17.0.1'}
path.write_text(''.join('%s=%s\n'%(k,v) for k,v in values.items()));path.chmod(0o600)
conf=runtime/'odoo.conf'
conf.write_text('[options]\nadmin_passwd = %s\naddons_path = /mnt/extra-addons\ndata_dir = /var/lib/odoo\nlist_db = False\nproxy_mode = True\nmax_cron_threads = 0\nlimit_time_real = 120\nlimit_time_cpu = 90\n'%(secrets.token_hex(32)))
# Read-only bind mounted into the application; parent directory remains private.
conf.chmod(0o644)
(runtime/'DEPLOYMENT_ID').write_text('inspection_public\n')
print('Deployment configuration created; secrets are private.')
