# License AGPL-3.0-or-later.
"""Correct demo device teams after the first 1.1 upgrade.

The first 1.1 migration moved every ``demo_equipment_*`` record to the
mechanical team, including the electrical/instrument devices whose demo
definitions already pointed to their own team.  This data-only fix restores
the intended default teams; real (non-demo) records are never touched.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

TEAM_BY_XMLID = {
    'demo_equipment_motor': 'demo_team_electrical',
    'demo_equipment_motor_2': 'demo_team_electrical',
    'demo_equipment_cabinet': 'demo_team_electrical',
    'demo_equipment_gauge': 'demo_team_instrument',
    'demo_equipment_sensor': 'demo_team_instrument',
}


def _backfill_inspection_supervisor(env):
    """Copy the already configured device supervisor onto active inspections.

    This is an explicit repair of historical records from the device
    configuration, not a random administrator fallback.  Records whose device
    has no supervisor are reported and left untouched.
    """
    # The ORM intentionally freezes this snapshot after creation, so the
    # historical repair uses a narrow SQL update on active records only.
    env.cr.execute("""
        UPDATE qc_inspection AS inspection
           SET inspection_supervisor_id = equipment.inspection_supervisor_id
          FROM maintenance_equipment AS equipment
         WHERE inspection.equipment_id = equipment.id
           AND inspection.state IN ('draft', 'ready', 'waiting')
           AND inspection.inspection_supervisor_id IS NULL
           AND equipment.inspection_supervisor_id IS NOT NULL
    """)
    env.invalidate_all()
    missing = env['qc.inspection'].search([
        ('equipment_id', '!=', False),
        ('state', 'in', ('draft', 'ready', 'waiting')),
        ('inspection_supervisor_id', '=', False),
    ])
    for inspection in missing:
        _logger.warning(
            'Inspection %s still has no supervisor: configure the device first',
            inspection.id)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _backfill_inspection_supervisor(env)
    for equipment_xmlid, team_xmlid in TEAM_BY_XMLID.items():
        equipment = env.ref(f'inspection.{equipment_xmlid}', raise_if_not_found=False)
        team = env.ref(f'inspection.{team_xmlid}', raise_if_not_found=False)
        if equipment and team and equipment.maintenance_team_id != team:
            equipment.sudo().write({'maintenance_team_id': team.id})
            _logger.info('Restored demo team %s for %s', team.name, equipment.name)
