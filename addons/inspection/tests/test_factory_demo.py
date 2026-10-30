# License AGPL-3.0-or-later.
from collections import Counter

from odoo.tests import TransactionCase, tagged

from ..demo_seed import seed_factory_history


@tagged('post_install', '-at_install')
class TestFactoryDemo(TransactionCase):
    """Validate the demonstration as a connected business story."""

    def setUp(self):
        super().setUp()
        if not self.env.ref('inspection.factory_equipment_p01', raise_if_not_found=False):
            self.skipTest('Factory demonstration data is not installed.')

    def ref(self, name):
        return self.env.ref('inspection.factory_' + name)

    def test_complete_story_and_states(self):
        records = self.env['qc.inspection'].browse([
            self.ref('history_%02d' % i).id for i in range(1, 25)])
        self.assertEqual(Counter(records.mapped('state')), {
            'success': 12, 'failed': 4, 'waiting': 3,
            'ready': 3, 'draft': 1, 'canceled': 1,
        })
        equipment_ids = self.env['ir.model.data'].search([
            ('module', '=', 'inspection'), ('name', '=like', 'factory_equipment_%')])
        self.assertEqual(len(equipment_ids), 12)
        self.assertEqual(len(records.mapped('maintenance_request_id')), 3)

    def test_snapshot_and_latest_summary(self):
        old_pressure = self.ref('history_01').inspection_lines.filtered(
            lambda line: line.test_line == self.ref('question_pump_daily_pressure'))
        self.assertEqual(old_pressure.max_value, 9)
        self.assertEqual(self.ref('question_pump_daily_pressure').max_value, 8)
        fan = self.ref('equipment_f01')
        self.assertEqual(fan.inspection_last_completed_id.state, 'success')
        self.assertEqual(fan.inspection_waiting_count, 1)

    def test_pending_tasks_and_native_activity(self):
        records = self.env['qc.inspection'].browse([
            self.ref('history_%02d' % i).id for i in range(1, 25)])
        for record in records:
            activities = self.env['mail.activity'].sudo().search([('inspection_id', '=', record.id)])
            if record.state in ('draft', 'ready', 'waiting'):
                self.assertEqual(len(activities), 1)
                self.assertEqual(activities.res_model, 'maintenance.equipment')
                self.assertEqual(activities.res_id, record.equipment_id.id)
                expected = record.inspection_supervisor_id if record.state == 'waiting' else record.user
                self.assertEqual(activities.user_id, expected)
            else:
                self.assertFalse(activities)

    def test_repair_round_trip_and_reassignment(self):
        for index in (11, 22, 23):
            inspection = self.ref('history_%02d' % index)
            request = self.ref('repair_from_%02d' % index)
            self.assertEqual(inspection.maintenance_request_id, request)
            self.assertEqual(request.inspection_id, inspection)
        request = self.ref('repair_from_23')
        self.assertEqual(request.maintenance_team_id, self.ref('team_instrument'))
        self.assertEqual(request.user_id, self.ref('user_instrument'))

    def test_seed_is_repeatable_without_resetting_user_work(self):
        equipment = self.ref('equipment_p01')
        equipment.note = 'Visitor observation to preserve'
        saved_note = equipment.note
        before = self.env['qc.inspection'].search_count([])
        seed_factory_history(self.env)
        self.assertEqual(self.env['qc.inspection'].search_count([]), before)
        self.assertEqual(equipment.note, saved_note)

    def test_inspector_and_supervisor_have_distinct_worklists(self):
        inspector = self.ref('user_inspector')
        manager = self.ref('user_manager')
        mine = self.env['qc.inspection'].with_user(inspector).search([('equipment_id', '!=', False)])
        self.assertTrue(mine)
        self.assertTrue(all(record.user == inspector for record in mine))
        all_records = self.env['qc.inspection'].with_user(manager).search([('equipment_id', '!=', False)])
        self.assertGreater(len(all_records), len(mine))
