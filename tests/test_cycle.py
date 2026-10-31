# License AGPL-3.0-or-later.
from datetime import date
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, new_test_user
from odoo.addons.base.tests.common import BaseCommon


@tagged('post_install', '-at_install')
class TestInspectionCycle(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.team = cls.env['maintenance.team'].create(
            {'name': '周期团队', 'company_id': cls.env.company.id})
        cls.category = cls.env['maintenance.equipment.category'].create(
            {'name': '周期水泵', 'company_id': cls.env.company.id})
        cls.supervisor = new_test_user(
            cls.env, login='cycle_supervisor', groups='inspection.group_manager',
            company_id=cls.env.company.id)
        cls.test = cls.env['qc.test'].create({
            'name': '周期方案', 'inspection_equipment_use': True,
            'equipment_category_ids': [Command.set(cls.category.ids)],
            'test_lines': [
                Command.create({'name': '是否渗漏', 'type': 'qualitative', 'ql_values': [
                    Command.create({'name': '无渗漏', 'ok': True}),
                    Command.create({'name': '渗漏', 'ok': False})]}),
                Command.create({'name': '计数', 'type': 'quantitative',
                                'min_value': 0, 'max_value': 10, 'uom_id': cls.unit.id}),
            ],
        })
        cls.today = cls.env['maintenance.equipment']._inspection_business_today()
        # Demo devices are also enabled in a demo database; keep this suite
        # deterministic by pausing them (rolled back with the test transaction).
        cls.env['maintenance.equipment'].sudo().search([
            ('inspection_cycle_enabled', '=', True),
        ]).write({'inspection_cycle_paused': True})

    def cycle_equipment(self, **kw):
        vals = {
            'name': '周期设备',
            'company_id': self.env.company.id,
            'maintenance_team_id': self.team.id,
            'category_id': self.category.id,
            'inspection_test_id': self.test.id,
            'inspection_supervisor_id': self.supervisor.id,
            'inspection_cycle_enabled': True,
            'inspection_cycle_interval': 1,
            'inspection_cycle_unit': 'days',
            'inspection_cycle_start_date': self.today,
        }
        vals.update(kw)
        return self.env['maintenance.equipment'].create(vals)

    def run_cron(self):
        return self.env['qc.inspection']._cron_generate_cycle_inspections()

    def test_occurrence_daily_weekly_monthly(self):
        daily = self.cycle_equipment(
            inspection_cycle_unit='days', inspection_cycle_interval=3,
            inspection_cycle_start_date=date(2026, 1, 1))
        self.assertEqual(daily._inspection_cycle_occurrence(0), date(2026, 1, 1))
        self.assertEqual(daily._inspection_cycle_occurrence(2), date(2026, 1, 7))
        weekly = self.cycle_equipment(
            inspection_cycle_unit='weeks', inspection_cycle_interval=2,
            inspection_cycle_start_date=date(2026, 3, 2))
        self.assertEqual(weekly._inspection_cycle_occurrence(1), date(2026, 3, 16))
        monthly = self.cycle_equipment(
            inspection_cycle_unit='months', inspection_cycle_start_date=date(2026, 1, 31))
        self.assertEqual(monthly._inspection_cycle_occurrence(1), date(2026, 2, 28))
        self.assertEqual(monthly._inspection_cycle_occurrence(2), date(2026, 3, 31))
        self.assertEqual(monthly._inspection_cycle_occurrence(13), date(2027, 2, 28))
        self.assertEqual(monthly._inspection_cycle_index_on_or_before(date(2026, 3, 15)), 1)

    def test_month_end_and_leap_year(self):
        leap = self.cycle_equipment(
            inspection_cycle_unit='months', inspection_cycle_start_date=date(2024, 1, 31))
        self.assertEqual(leap._inspection_cycle_occurrence(1), date(2024, 2, 29))
        self.assertEqual(leap._inspection_cycle_occurrence(2), date(2024, 3, 31))
        normal = self.cycle_equipment(
            inspection_cycle_unit='months', inspection_cycle_start_date=date(2025, 1, 31))
        self.assertEqual(normal._inspection_cycle_occurrence(1), date(2025, 2, 28))
        self.assertEqual(normal._inspection_cycle_occurrence(2), date(2025, 3, 31))

    def test_not_due_does_not_generate(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today + relativedelta(days=5))
        self.assertEqual(equipment.inspection_cycle_next_date, self.today + relativedelta(days=5))
        self.assertEqual(self.run_cron(), 0)
        self.assertFalse(equipment.inspection_ids)

    def test_due_generates_only_latest_period(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today - relativedelta(days=10))
        self.assertEqual(self.run_cron(), 1)
        self.assertEqual(len(equipment.inspection_ids), 1)
        inspection = equipment.inspection_ids
        self.assertEqual(inspection.inspection_source, 'cron')
        self.assertEqual(inspection.inspection_date, self.today)
        self.assertEqual(inspection.inspection_cycle_date, self.today)
        self.assertEqual(equipment.inspection_cycle_next_date,
                         self.today + relativedelta(days=1))

    def test_repeat_is_idempotent_and_unique_index(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today - relativedelta(days=2))
        self.run_cron()
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 1)
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env['qc.inspection'].create({
                'equipment_id': equipment.id,
                'inspection_date': self.today,
                'inspection_source': 'cron',
                'inspection_cycle_date': self.today,
            })

    def test_failure_does_not_advance_next_date(self):
        equipment = self.cycle_equipment()
        self.assertEqual(equipment.inspection_cycle_next_date, self.today)
        inspection_model = type(self.env['qc.inspection'])
        with patch.object(inspection_model, 'create', side_effect=UserError('boom')):
            self.run_cron()
        self.assertEqual(equipment.inspection_cycle_next_date, self.today)
        self.assertFalse(equipment.inspection_ids)
        self.assertIn('boom', equipment.inspection_cycle_last_error)
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 1)
        self.assertFalse(equipment.inspection_cycle_last_error)

    def test_pause_and_resume(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today - relativedelta(days=5),
            inspection_cycle_paused=True)
        self.assertEqual(self.run_cron(), 0)
        self.assertFalse(equipment.inspection_ids)
        equipment.inspection_cycle_paused = False
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 1)
        self.assertEqual(equipment.inspection_ids.inspection_date, self.today)

    def test_single_switch_preserves_overdue_schedule(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today - relativedelta(days=5))
        overdue = self.today - relativedelta(days=3)
        equipment.inspection_cycle_next_date = overdue
        equipment.inspection_cycle_active = False
        self.assertFalse(equipment.inspection_cycle_active)
        self.assertEqual(equipment.inspection_cycle_next_date, overdue)
        self.assertEqual(self.run_cron(), 0)
        equipment.inspection_cycle_active = True
        self.assertEqual(equipment.inspection_cycle_next_date, overdue)
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 1)
        self.assertEqual(equipment.inspection_ids.inspection_date, self.today)

    def test_single_switch_first_enable_and_permissions(self):
        equipment = self.cycle_equipment(inspection_cycle_enabled=False)
        self.assertFalse(equipment.inspection_cycle_active)
        equipment.with_user(self.supervisor).inspection_cycle_active = True
        self.assertTrue(equipment.inspection_cycle_enabled)
        self.assertFalse(equipment.inspection_cycle_paused)
        self.assertEqual(equipment.inspection_cycle_next_date, self.today)
        inspector = new_test_user(self.env, login='switch_inspector',
                                  groups='inspection.group_user')
        with self.assertRaises(AccessError):
            equipment.with_user(inspector).inspection_cycle_active = False

    def test_cancel_does_not_auto_rebuild_but_manager_can(self):
        equipment = self.cycle_equipment()
        self.run_cron()
        inspection = equipment.inspection_ids
        inspection.action_cancel()
        next_date = equipment.inspection_cycle_next_date
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 1)
        self.assertEqual(equipment.inspection_cycle_next_date, next_date)
        action = inspection.action_rebuild_cycle_inspection()
        rebuilt = self.env['qc.inspection'].browse(action['res_id'])
        self.assertNotEqual(rebuilt, inspection)
        self.assertEqual(rebuilt.inspection_cycle_date, inspection.inspection_cycle_date)
        self.assertEqual(equipment.inspection_cycle_next_date, next_date)

    def test_config_change_keeps_history(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today - relativedelta(days=3))
        self.run_cron()
        inspection = equipment.inspection_ids
        original = (inspection.inspection_date, inspection.inspection_cycle_date, inspection.test)
        equipment.write({
            'inspection_cycle_interval': 2,
            'inspection_cycle_unit': 'weeks',
            'inspection_cycle_start_date': self.today - relativedelta(days=1),
        })
        inspection.invalidate_recordset()
        self.assertEqual(
            (inspection.inspection_date, inspection.inspection_cycle_date, inspection.test), original)
        self.assertGreater(equipment.inspection_cycle_next_date, self.today)

    def test_temporary_inspection_does_not_consume_cycle(self):
        equipment = self.cycle_equipment()
        manual = self.env['qc.inspection'].generate_for_equipment(equipment)
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 2)
        cron_record = equipment.inspection_ids.filtered(
            lambda record: record.inspection_source == 'cron')
        self.assertEqual(cron_record.inspection_cycle_date, self.today)
        self.assertFalse(manual.inspection_cycle_date)

    def test_batch_and_cycle_coexist_and_batch_is_deduped(self):
        equipment = self.cycle_equipment()
        batch_model = self.env['qc.inspection']
        batch = batch_model.generate_for_equipment(equipment, self.today, source='batch')
        self.assertEqual(batch_model.generate_for_equipment(equipment, self.today, source='batch'), batch)
        self.run_cron()
        self.assertEqual(len(equipment.inspection_ids), 2)
        self.assertEqual(set(equipment.inspection_ids.mapped('inspection_source')), {'batch', 'cron'})

    def test_batch_wizard_temporary_and_due_modes(self):
        equipment = self.cycle_equipment()
        # Due mode reuses the shared cycle entry point.
        due_wizard = self.env['inspection.generate'].create({
            'mode': 'due',
            'equipment_ids': [Command.set(equipment.ids)],
        })
        action = due_wizard.action_generate()
        record = self.env['qc.inspection'].browse(action['domain'][0][2])
        self.assertEqual(record.inspection_source, 'cron')
        self.assertEqual(record.inspection_date, self.today)
        self.assertEqual(equipment.inspection_cycle_next_date,
                         self.today + relativedelta(days=1))
        # Temporary mode accepts another applicable scheme and does not touch
        # the plan: another scheme may legitimately coexist with the cycle.
        other_test = self.test.copy({'name': '临时专项方案'})
        temp_wizard = self.env['inspection.generate'].create({
            'mode': 'temporary',
            'inspection_date': self.today,
            'equipment_ids': [Command.set(equipment.ids)],
            'test_id': other_test.id,
        })
        temp_action = temp_wizard.action_generate()
        temporary = self.env['qc.inspection'].browse(temp_action['domain'][0][2])
        self.assertEqual(temporary.inspection_source, 'batch')
        self.assertEqual(temporary.test, other_test)
        self.assertFalse(temporary.inspection_cycle_date)
        self.assertEqual(equipment.inspection_cycle_next_date,
                         self.today + relativedelta(days=1))

    def test_equipment_summary_fields(self):
        equipment = self.cycle_equipment(inspection_cycle_enabled=False)
        self.assertFalse(equipment.inspection_last_completed_id)
        self.assertFalse(equipment.inspection_last_date)
        self.assertEqual(equipment.inspection_waiting_count, 0)
        self.assertEqual(equipment.inspection_ready_count, 0)
        self.assertFalse(equipment.inspection_recent_ids)
        model = self.env['qc.inspection']
        old = model.generate_for_equipment(equipment, self.today - relativedelta(days=10))
        old.action_todo()
        for line in old.inspection_lines:
            if line.question_type == 'qualitative':
                line.qualitative_value = line.possible_ql_values.filtered(lambda a: a.ok)[:1]
            else:
                line.quantitative_value = 5
        old.action_confirm()
        self.assertEqual(old.state, 'success')
        old.sudo().write({'date_done': fields.Datetime.to_datetime(
            self.today - relativedelta(days=10))})
        recent = model.generate_for_equipment(equipment, self.today)
        recent.action_todo()
        for line in recent.inspection_lines:
            if line.question_type == 'qualitative':
                line.qualitative_value = line.possible_ql_values.filtered(lambda a: not a.ok)[:1]
            else:
                line.quantitative_value = 9
        recent.action_confirm()
        self.assertEqual(recent.state, 'waiting')
        draft = model.generate_for_equipment(equipment, self.today - relativedelta(days=1))
        equipment.invalidate_recordset()
        self.assertEqual(equipment.inspection_last_completed_id, old)
        self.assertEqual(equipment.inspection_last_date,
                         (self.today - relativedelta(days=10)))
        self.assertEqual(equipment.inspection_waiting_count, 1)
        self.assertEqual(equipment.inspection_ready_count, 1)
        self.assertEqual(len(equipment.inspection_recent_ids), 3)
        self.assertEqual(equipment.inspection_recent_ids[0], recent)
        self.assertEqual(equipment.inspection_recent_ids[1], draft)
        self.assertNotEqual(draft, recent)

    def test_cron_batches_many_equipments(self):
        equipments = self.env['maintenance.equipment']
        for index in range(55):
            equipments |= self.cycle_equipment(name='批量设备 %s' % index)
        self.assertEqual(self.run_cron(), 55)
        for equipment in equipments:
            self.assertEqual(len(equipment.inspection_ids), 1)
            self.assertEqual(equipment.inspection_cycle_next_date,
                             self.today + relativedelta(days=1))

    def test_tests_action_binds_list_and_search(self):
        action = self.env.ref('inspection.action_tests')
        list_binding = self.env['ir.actions.act_window.view'].search([
            ('act_window_id', '=', action.id), ('view_mode', '=', 'list')], limit=1)
        form_binding = self.env['ir.actions.act_window.view'].search([
            ('act_window_id', '=', action.id), ('view_mode', '=', 'form')], limit=1)
        self.assertTrue(list_binding and form_binding)
        self.assertLess(list_binding.sequence, form_binding.sequence)
        self.assertEqual(list_binding.view_id.model, 'qc.test')
        self.assertIn('equipment_category_ids', list_binding.view_id.arch)
        self.assertTrue(action.search_view_id)
        self.assertIn('equipment_category_ids', action.search_view_id.arch)

    def test_due_wizard_without_due_equipment_raises(self):
        equipment = self.cycle_equipment(
            inspection_cycle_start_date=self.today + relativedelta(days=5))
        wizard = self.env['inspection.generate'].create({
            'mode': 'due', 'equipment_ids': [Command.set(equipment.ids)]})
        with self.assertRaises(UserError):
            wizard.action_generate()

    def test_validation(self):
        base = {
            'name': '校验设备', 'company_id': self.env.company.id,
            'category_id': self.category.id, 'inspection_test_id': self.test.id,
            'inspection_supervisor_id': self.supervisor.id,
            'inspection_cycle_enabled': True, 'inspection_cycle_interval': 1,
            'inspection_cycle_unit': 'days', 'inspection_cycle_start_date': self.today,
        }
        for override in (
            {'category_id': False},
            {'inspection_test_id': False},
            {'inspection_supervisor_id': False},
            {'inspection_cycle_interval': 0},
            {'inspection_cycle_interval': -1},
            {'inspection_cycle_start_date': False},
        ):
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.env['maintenance.equipment'].create({**base, **override})
        equipment = self.cycle_equipment()
        fan_category = self.env['maintenance.equipment.category'].create({'name': '周期风机'})
        fan_test = self.test.copy({'name': '风机方案', 'equipment_category_ids': [Command.set(fan_category.ids)]})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            equipment.write({'inspection_test_id': fan_test.id})

    def test_inspector_cannot_write_cycle_configuration(self):
        equipment = self.cycle_equipment()
        inspector = new_test_user(
            self.env, login='cycle_inspector', groups='inspection.group_user',
            company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            equipment.with_user(inspector).write({'inspection_cycle_paused': True})
        with self.assertRaises(AccessError):
            equipment.with_user(inspector).write({'inspection_cycle_next_date': self.today})
        equipment.with_user(self.supervisor).write({'inspection_cycle_paused': True})
        self.assertTrue(equipment.inspection_cycle_paused)

    def test_company_isolation(self):
        company = self.env['res.company'].create({'name': '周期其他公司'})
        category = self.env['maintenance.equipment.category'].create(
            {'name': '其他公司类别', 'company_id': company.id})
        supervisor = new_test_user(
            self.env, login='cycle_other_supervisor', groups='inspection.group_manager',
            company_id=company.id)
        test = self.env['qc.test'].create({
            'name': '其他公司方案', 'company_id': company.id, 'inspection_equipment_use': True,
            'equipment_category_ids': [Command.set(category.ids)],
            'test_lines': [Command.create({'name': '数值', 'type': 'quantitative', 'uom_id': self.unit.id})],
        })
        equipment = self.env['maintenance.equipment'].create({
            'name': '其他公司设备', 'company_id': company.id, 'category_id': category.id,
            'inspection_test_id': test.id, 'inspection_supervisor_id': supervisor.id,
            'inspection_cycle_enabled': True, 'inspection_cycle_start_date': self.today,
            'inspection_cycle_interval': 1, 'inspection_cycle_unit': 'days',
        })
        with self.assertRaises(AccessError):
            self.env['qc.inspection'].with_user(self.supervisor).generate_for_equipment(equipment)

    def test_due_action_and_permissions(self):
        equipment = self.cycle_equipment()
        action = equipment.action_generate_due_cycle_inspection()
        self.assertEqual(action['res_model'], 'qc.inspection')
        self.assertEqual(action['res_id'], equipment.inspection_ids.id)
        inspector = new_test_user(
            self.env, login='cycle_due_inspector', groups='inspection.group_user',
            company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            equipment.with_user(inspector).action_generate_due_cycle_inspection()
