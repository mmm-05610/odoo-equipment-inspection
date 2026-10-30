"""Run only in the new demo candidate; never import the learner's database."""
import os
import secrets
from pathlib import Path

exec(compile(Path('/opt/inspection/load_factory_demo.py').read_text(), 'load_factory_demo.py', 'exec'))
# Two intentional visitor accounts. Other people remain assignable, but their
# sample passwords must not provide additional public login paths.
public_logins = {'inspection.manager', 'inspection.inspector'}
for user in env['res.users'].sudo().with_context(active_test=False).search([('share', '=', False)]):
    if user.login not in public_logins:
        user.password = secrets.token_urlsafe(40)
config = env['ir.config_parameter'].sudo()
config.set_param('web.base.url', os.environ['DEMO_ORIGIN'])
config.set_param('web.base.url.freeze', 'True')
config.set_param('auth_signup.invitation_scope', 'b2b')
# Different database identity invalidates old sessions after a reset.
config.set_param('database.secret', secrets.token_hex(32))
env['ir.cron'].sudo().search([]).write({'active': False})
env['ir.mail_server'].sudo().search([]).write({'active': False})
expected = {'maintenance.equipment': 12, 'maintenance.team': 3,
            'qc.test': 9, 'qc.inspection': 24, 'maintenance.request': 8}
for model, count in expected.items():
    actual = env[model].search_count([])
    if actual != count:
        raise RuntimeError('%s: expected %s, found %s' % (model, count, actual))
env.cr.commit()
print('PUBLIC_DEMO_READY')
