"""Run with Odoo shell in a fresh inspection database without vendor demo data.

This intentionally refuses to replace an existing business database.
Example: docker compose ... run --rm -T web odoo shell -d inspection_factory --no-http < scripts/load_factory_demo.py
"""
import json
from odoo.tools import convert_file
from odoo.addons.inspection.demo_seed import seed_factory_history

seeded = env['ir.config_parameter'].sudo().get_param('inspection.factory_demo_seeded')
if not seeded:
    if env['maintenance.equipment'].search_count([]) or env['qc.inspection'].search_count([]):
        raise RuntimeError('Factory demo requires an empty business database; existing records were not changed.')
    for path in ('demo/inspection_lang_demo.xml', 'demo/factory_demo.xml'):
        convert_file(env, 'inspection', path, {}, mode='init', noupdate=True)
    env['ir.module.module'].search([('state', '=', 'installed')])._update_translations(filter_lang=['zh_CN'], overwrite=True)
    env.ref('base.main_company').name = '青禾制造（演示）'
    env.ref('base.user_admin').write({'lang': 'zh_CN', 'tz': 'Asia/Shanghai'})
    env['ir.config_parameter'].set_param('inspection.business_timezone', 'Asia/Shanghai')
    seed_factory_history(env)
    # Keep demonstration emails local. Normal chatter and activities still work.
    env['ir.mail_server'].search([]).write({'active': False})
    env.cr.commit()
# The native empty starter team is not part of this factory. Never archive it
# if someone has already assigned equipment or requests to it.
starter = env.ref('maintenance.equipment_team_maintenance', raise_if_not_found=False)
if starter and not starter.equipment_ids and not starter.request_ids:
    starter.active = False
env.cr.commit()
print(json.dumps({model: env[model].search_count([]) for model in (
    'maintenance.equipment', 'maintenance.team', 'qc.test', 'qc.inspection', 'maintenance.request')}, ensure_ascii=False))
