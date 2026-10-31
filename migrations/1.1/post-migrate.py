# License AGPL-3.0-or-later.
"""Convert the old daily flag to the configurable cycle and sync new features.

Rules (documented for learners, executed exactly once):

* ``inspection_daily`` enabled  -> enabled cycle, every 1 day, anchor today.
  This keeps the previous "one inspection per day" behaviour and cannot create
  an unbounded backlog: the first planned date is today.
* ``inspection_daily`` disabled -> cycle stays disabled.
* Existing inspections, answers, maintenance links and the standard snapshots
  are never modified.
* Legacy cron-generated inspections get ``inspection_cycle_date`` backfilled so
  the new scheduler recognises that occurrence as already generated.
* The old cron record is repointed to the shared cycle scheduler, and legacy
  database indexes that would block temporary/cycle coexistence are dropped.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

DEMO_SUPERVISOR_XMLID = 'inspection.demo_user_manager'


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
    """, [table, column])
    return bool(cr.fetchone())


def _set_demo_supervisors(env):
    """Known project demo devices get a clearly documented supervisor.

    This is not a random fallback: only records from this addon's demo module
    are touched; real equipment without a supervisor is reported, not guessed.
    """
    supervisor = env.ref(DEMO_SUPERVISOR_XMLID, raise_if_not_found=False)
    if not supervisor:
        return
    equipments = env['maintenance.equipment'].with_context(active_test=False).search([
        ('inspection_supervisor_id', '=', False),
    ])
    demo_equipments = env['maintenance.equipment']
    for equipment in equipments:
        if env['ir.model.data'].search_count([
            ('module', '=', 'inspection'),
            ('model', '=', 'maintenance.equipment'),
            ('res_id', '=', equipment.id),
        ], limit=1):
            demo_equipments |= equipment
    if demo_equipments:
        demo_equipments.sudo().write({'inspection_supervisor_id': supervisor.id})


def _set_demo_teams(env):
    """Assign the project demo devices to the mechanical team on upgrade.

    noupdate demo records keep their old values on upgrade, so the new demo
    team reference is applied explicitly here (demo records only).
    """
    team = env.ref('inspection.demo_team_mechanical', raise_if_not_found=False)
    if not team:
        return
    legacy_team = env.ref('inspection.demo_team', raise_if_not_found=False)
    if not legacy_team:
        return
    # Only devices that still point to the old generic demo team are migrated;
    # the electrical/instrument demo devices already have their real team in
    # the newer demo files and must never be overwritten here.
    demo_equipments = env['maintenance.equipment'].search([
        ('maintenance_team_id', '=', legacy_team.id),
    ])
    if demo_equipments:
        demo_equipments.sudo().write({'maintenance_team_id': team.id})
    if not legacy_team.equipment_ids:
        legacy_team.sudo().write({'active': False})


def _convert_daily_flag(env):
    if not _column_exists(env.cr, 'maintenance_equipment', 'inspection_daily'):
        return
    env.cr.execute("""
        SELECT id FROM maintenance_equipment
        WHERE inspection_daily IS TRUE ORDER BY id
    """)
    equipments = env['maintenance.equipment'].browse([row[0] for row in env.cr.fetchall()])
    converted = 0
    for equipment in equipments:
        if not equipment.inspection_test_id or not equipment.inspection_supervisor_id:
            _logger.warning(
                'Cannot convert inspection_daily for equipment %s: missing scheme or supervisor',
                equipment.id)
            continue
        try:
            with env.cr.savepoint():
                equipment.write({
                    'inspection_cycle_enabled': True,
                    'inspection_cycle_interval': 1,
                    'inspection_cycle_unit': 'days',
                    'inspection_cycle_start_date': equipment._inspection_business_today(),
                })
                converted += 1
        except Exception:  # noqa: BLE001 - one bad legacy row must not block the upgrade
            _logger.exception('Daily -> cycle conversion failed for equipment %s', equipment.id)
    if converted:
        _logger.info('Converted %s equipment(s) from daily flag to 1-day cycle', converted)


def _backfill_cycle_dates(env):
    env.cr.execute("""
        UPDATE qc_inspection
        SET inspection_cycle_date = inspection_date
        WHERE inspection_source = 'cron'
          AND inspection_cycle_date IS NULL
          AND inspection_date IS NOT NULL
    """)
    if env.cr.rowcount:
        _logger.info('Backfilled %s legacy cron inspection cycle dates', env.cr.rowcount)


def _update_legacy_cron(env):
    cron = env.ref('inspection.cron_daily', raise_if_not_found=False)
    if cron:
        cron.write({
            'name': '设备检查：按周期生成',
            'code': 'model._cron_generate_cycle_inspections()',
            'active': False,
        })
    # The new shared cron is created by data/cron.xml; make sure a legacy
    # database does not keep two independent schedulers with different code.
    new_cron = env.ref('inspection.cron_cycle', raise_if_not_found=False)
    if new_cron and cron and cron != new_cron:
        cron.unlink()


def _drop_legacy_indexes(env):
    # Old "one batch/cron inspection per equipment and date" index: the plan
    # now uses (equipment, cycle_date) for cycles and (equipment, date, test)
    # for batch entries, so a temporary inspection may coexist with a cycle.
    env.cr.execute('DROP INDEX IF EXISTS qc_inspection_equipment_daily_unique')


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _set_demo_supervisors(env)
    _set_demo_teams(env)
    _drop_legacy_indexes(env)
    _backfill_cycle_dates(env)
    _convert_daily_flag(env)
    _update_legacy_cron(env)
    env['qc.inspection']._sync_inspection_activities()
