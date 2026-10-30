# License AGPL-3.0-or-later.
"""Equipment inspection todos on native maintenance.equipment activities."""
from datetime import date

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon

INSPECTION_ACTIVITY_XMLID = 'inspection.mail_act_equipment_inspection'
WAITING_ACTIVITY_XMLID = 'inspection.mail_act_equipment_inspection_waiting'


@tagged('post_install', '-at_install')
class TestInspectionActivity(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.manager = new_test_user(
            cls.env, login='activity_manager', groups='inspection.group_manager',
            company_id=cls.env.company.id)
        cls.inspector = new_test_user(
            cls.env, login='activity_inspector', groups='inspection.group_user',
            company_id=cls.env.company.id)
        cls.other_inspector = new_test_user(
            cls.env, login='activity_inspector_2', groups='inspection.group_user',
            company_id=cls.env.company.id)
        cls.other_manager = new_test_user(
            cls.env, login='activity_manager_2', groups='inspection.group_manager',
            company_id=cls.env.company.id)
        cls.team = cls.env['maintenance.team'].create({
            'name': '活动测试团队', 'company_id': cls.env.company.id})
        cls.category = cls.env['maintenance.equipment.category'].create({
            'name': '活动测试水泵', 'company_id': cls.env.company.id,
            'technician_user_id': cls.manager.id})
        cls.test = cls.env['qc.test'].create({
            'name': '活动测试方案',
            'inspection_equipment_use': True,
            'equipment_category_ids': [Command.set(cls.category.ids)],
            'test_lines': [
                Command.create({'name': '是否渗漏', 'type': 'qualitative', 'ql_values': [
                    Command.create({'name': '无渗漏', 'ok': True}),
                    Command.create({'name': '渗漏', 'ok': False}),
                ]}),
                Command.create({'name': '计数', 'type': 'quantitative',
                                'min_value': 0, 'max_value': 10, 'uom_id': cls.unit.id}),
            ],
        })
        # Demo fixtures may have cycle devices: make sure a concurrent cron run
        # cannot generate extra inspections while this test class executes.
        # The class transaction is rolled back, so the demo data is untouched.
        cls.env['maintenance.equipment'].sudo().search([
            ('inspection_cycle_enabled', '=', True),
            ('inspection_cycle_paused', '=', False),
        ]).write({'inspection_cycle_paused': True})
        cls.equipment = cls._new_equipment('活动测试设备')

    @classmethod
    def _new_equipment(cls, name, **values):
        defaults = {
            'name': name,
            'company_id': cls.env.company.id,
            'maintenance_team_id': cls.team.id,
            'category_id': cls.category.id,
            'inspection_test_id': cls.test.id,
            'inspection_supervisor_id': cls.manager.id,
            'technician_user_id': cls.inspector.id,
            'owner_user_id': cls.manager.id,
        }
        defaults.update(values)
        return cls.env['maintenance.equipment'].create(defaults)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _record(self, equipment=None, user=None, **values):
        record = {
            'equipment_id': (equipment or self.equipment).id,
            'user': (user or self.inspector).id,
        }
        record.update(values)
        return self.env['qc.inspection'].create(record)

    def _fill(self, record, failed=False):
        for line in record.inspection_lines:
            if line.question_type == 'qualitative':
                line.qualitative_value = line.possible_ql_values.filtered(
                    lambda answer: answer.ok != failed)[:1]
            else:
                line.quantitative_value = 5
        record.internal_notes = '活动测试：现场检查完成。'

    def _answer(self, record, failed=False):
        record.action_todo()
        self._fill(record, failed=failed)

    def _activity(self, record):
        return self.env['mail.activity'].sudo().search([('inspection_id', '=', record.id)])

    def _management_type(self):
        return self.env.ref(INSPECTION_ACTIVITY_XMLID)

    def _waiting_type(self):
        return self.env.ref(WAITING_ACTIVITY_XMLID)

    # ------------------------------------------------------------------
    # Generation -> activity
    # ------------------------------------------------------------------
    def test_manual_create_schedules_activity(self):
        record = self._record()
        activity = self._activity(record)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.inspection_id, record)
        self.assertEqual(activity.user_id, self.inspector)
        self.assertEqual(activity.date_deadline, record.inspection_date)
        self.assertEqual(activity.activity_type_id, self._management_type())
        self.assertEqual(activity.activity_type_id.res_model, 'maintenance.equipment')
        self.assertEqual(activity.res_model, 'maintenance.equipment')
        self.assertEqual(activity.res_id, self.equipment.id)
        self.assertTrue(activity.automated)
        self.assertIn(self.equipment.name, activity.summary)
        self.assertIn(record.inspection_test_name, activity.summary)
        note = str(activity.note)
        self.assertIn(record.name, note)
        self.assertIn(record.inspection_test_name, note)
        self.assertIn('/odoo/action-', note)
        self.assertIn(f'/{record.id}"', note)

    def test_deadline_uses_business_date(self):
        business_date = date(2026, 1, 15)
        record = self._record(inspection_date=business_date)
        self.assertEqual(self._activity(record).date_deadline, business_date)
        # Business date is frozen after creation: only a future change of that
        # rule would require updating date_deadline here.
        with self.assertRaises(UserError):
            record.write({'inspection_date': date(2026, 2, 1)})
        self.assertEqual(self._activity(record).date_deadline, business_date)

    def test_batch_and_cycle_generation_schedule_one_activity(self):
        today = self.env['maintenance.equipment']._inspection_business_today()
        inspection_model = self.env['qc.inspection']
        # Batch entry point: repeated generation returns the same record.
        batch = inspection_model.generate_for_equipment(self.equipment, today, 'batch')
        activity = self._activity(batch)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, self.inspector)
        self.assertEqual(activity.date_deadline, today)
        batch_again = inspection_model.generate_for_equipment(self.equipment, today, 'batch')
        self.assertEqual(batch_again, batch)
        self.assertEqual(len(self._activity(batch)), 1)
        # A temporary (cron without cycle date) inspection is a different
        # record by design; each inspection keeps its own single activity.
        temporary = inspection_model.generate_for_equipment(self.equipment, today, 'cron')
        self.assertNotEqual(temporary, batch)
        self.assertEqual(len(self._activity(batch)), 1)
        self.assertEqual(len(self._activity(temporary)), 1)
        # Cycle generation goes through the same create() path and is
        # de-duplicated by its plan date.
        cycle_equipment = self._new_equipment('周期活动设备')
        cycle_equipment.write({
            'inspection_cycle_enabled': True,
            'inspection_cycle_interval': 1,
            'inspection_cycle_unit': 'days',
            'inspection_cycle_start_date': today,
        })
        cycle = inspection_model.generate_cycle_for_equipment(cycle_equipment, today=today)
        self.assertTrue(cycle)
        self.assertEqual(cycle.inspection_cycle_date, today)
        self.assertEqual(len(self._activity(cycle)), 1)
        self.assertEqual(self._activity(cycle).date_deadline, cycle.inspection_date)
        cycle_again = inspection_model.generate_for_equipment(
            cycle_equipment, today, 'cron', cycle_date=today)
        self.assertEqual(cycle_again, cycle)
        self.assertEqual(len(self._activity(cycle)), 1)

    def test_repeated_sync_does_not_duplicate_activity(self):
        record = self._record()
        activity = self._activity(record)
        record.write({'internal_notes': '重复保存不应新增活动'})
        self.assertEqual(self._activity(record).ids, activity.ids)
        record._sync_inspection_activity()
        self.env['qc.inspection']._sync_inspection_activities()
        self.assertEqual(self._activity(record).ids, activity.ids)
        self.assertEqual(len(activity), 1)

    def test_two_inspections_have_independent_activities(self):
        first = self._record()
        second = self._record()
        self.assertEqual(len(self._activity(first)), 1)
        self.assertEqual(len(self._activity(second)), 1)
        self.assertNotEqual(self._activity(first), self._activity(second))
        self.assertEqual(self._activity(first).inspection_id, first)
        self.assertEqual(self._activity(second).inspection_id, second)
        self.assertIn(self._activity(first), self.equipment.activity_ids)
        self.assertIn(self._activity(second), self.equipment.activity_ids)

    def test_non_equipment_inspection_is_ignored(self):
        record = self.env['qc.inspection'].create({})
        self.assertFalse(record.equipment_id)
        self.assertFalse(self._activity(record))

    def test_plan_state_never_creates_activity(self):
        record = self._record()
        self._activity(record).unlink()
        # A future calendar plan is not a real inspection: the sync must not
        # create a todo for it (state "plan" is OCA's planned entry).
        self.env.cr.execute('UPDATE qc_inspection SET state = %s WHERE id = %s', ('plan', record.id))
        record.invalidate_recordset(['state'])
        self.assertEqual(record.state, 'plan')
        record._sync_inspection_activity()
        self.assertFalse(self._activity(record))

    # ------------------------------------------------------------------
    # Assignment changes
    # ------------------------------------------------------------------
    def test_reassignment_updates_activity_owner(self):
        record = self._record()
        activity = self._activity(record)
        record.write({'user': self.other_inspector.id})
        updated = self._activity(record)
        self.assertEqual(updated.ids, activity.ids)
        self.assertEqual(updated.user_id, self.other_inspector)
        self.assertEqual(updated.inspection_id, record)

    # ------------------------------------------------------------------
    # Inspection lifecycle -> activity lifecycle
    # ------------------------------------------------------------------
    def test_success_closes_activity(self):
        record = self._record()
        activity_id = self._activity(record).id
        self._answer(record)
        record.action_confirm()
        self.assertEqual(record.state, 'success')
        self.assertFalse(self.env['mail.activity'].browse(activity_id).exists())
        self.assertFalse(self._activity(record))
        self.assertNotIn(activity_id, self.equipment.activity_ids.ids)

    def test_waiting_reuses_activity_for_supervisor(self):
        record = self._record()
        original = self._activity(record)
        self._answer(record, failed=True)
        record.action_confirm()
        self.assertEqual(record.state, 'waiting')
        waiting = self._activity(record)
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting.ids, original.ids)
        self.assertEqual(waiting.activity_type_id, self._waiting_type())
        self.assertEqual(waiting.user_id, self.manager)
        self.assertEqual(waiting.date_deadline, record.inspection_date)
        self.assertIn('待确认异常', waiting.summary)
        # The guard now explains that a supervisor has to confirm.
        with self.assertRaises(UserError) as error:
            waiting.action_feedback()
        self.assertIn('检查主管', str(error.exception))

    def test_failed_approval_closes_waiting_activity(self):
        record = self._record()
        self._answer(record, failed=True)
        record.action_confirm()
        self.assertEqual(record.state, 'waiting')
        self.assertEqual(len(self._activity(record)), 1)
        record.action_approve()
        self.assertEqual(record.state, 'failed')
        self.assertFalse(self._activity(record))

    def test_cancel_removes_activity(self):
        draft = self._record()
        draft.action_cancel()
        self.assertEqual(draft.state, 'canceled')
        self.assertFalse(self._activity(draft))
        ready = self._record()
        ready.action_todo()
        self.assertEqual(len(self._activity(ready)), 1)
        ready.action_cancel()
        self.assertEqual(ready.state, 'canceled')
        self.assertFalse(self._activity(ready))

    # ------------------------------------------------------------------
    # Completion guard
    # ------------------------------------------------------------------
    def test_completion_is_blocked_while_inspection_open(self):
        record = self._record()
        activity = self._activity(record)
        for name in ('action_feedback', 'action_done', 'action_feedback_schedule_next'):
            with self.assertRaises(UserError) as error:
                getattr(activity, name)()
            self.assertIn(record.name, str(error.exception))
            self.assertIn('不能直接在活动上完成', str(error.exception))
        # The native mixin entry point is guarded as well.
        with self.assertRaises(UserError):
            self.equipment.activity_feedback([INSPECTION_ACTIVITY_XMLID])
        self.assertEqual(record.state, 'draft')
        self.assertEqual(len(self._activity(record)), 1)
        # Ready, and then waiting, both keep the guard active.
        record.action_todo()
        self.assertEqual(record.state, 'ready')
        with self.assertRaises(UserError):
            self._activity(record).action_feedback()
        self._fill(record, failed=True)
        record.action_confirm()
        self.assertEqual(record.state, 'waiting')
        with self.assertRaises(UserError):
            self._activity(record).action_feedback()
        record.action_approve()
        self.assertEqual(record.state, 'failed')
        self.assertFalse(self._activity(record))

    def test_completion_cannot_be_bypassed_with_context(self):
        # The RPC context is client supplied: no context key may bypass the
        # guard (there is no "sync" escape hatch on purpose).
        record = self._record()
        activity = self._activity(record)
        for key in ('inspection_activity_sync', 'mail_activity_quick_update'):
            with self.assertRaises(UserError):
                activity.with_context(**{key: True}).action_feedback()
        self.assertEqual(record.state, 'draft')
        self.assertEqual(len(self._activity(record)), 1)

    def test_repair_request_takes_over_after_failed(self):
        # After the abnormal inspection is confirmed failed, the maintenance
        # request workflow owns the repair task; the inspection reminder must
        # not stay behind as a duplicate.
        record = self._record()
        self._answer(record, failed=True)
        record.action_confirm()
        record.action_approve()
        self.assertEqual(record.state, 'failed')
        self.assertFalse(self._activity(record))
        record.action_create_repair()
        self.assertTrue(record.maintenance_request_id)
        self.assertFalse(self._activity(record))

    def test_manual_activity_keeps_native_behaviour(self):
        record = self._record()
        manual = self.equipment.activity_schedule(
            'mail.mail_activity_data_todo', summary='人工待办',
            date_deadline=fields.Date.today(), user_id=self.inspector.id)
        self.assertFalse(manual.inspection_id)
        manual.action_feedback()
        self.assertFalse(manual.active)
        # Lifecycle sync only ever touches activities linked to an inspection.
        untouched = self.equipment.activity_schedule(
            'mail.mail_activity_data_todo', summary='保留的人工待办',
            date_deadline=fields.Date.today(), user_id=self.inspector.id)
        record.action_cancel()
        self.assertTrue(untouched.active)

    def test_activity_visible_on_equipment_card(self):
        record = self._record()
        activity = self._activity(record)
        self.assertIn(activity.id, self.equipment.activity_ids.ids)
        self.assertEqual(self.equipment.activity_user_id, self.inspector)
        self.assertEqual(activity.activity_type_id.category, 'default')

    def test_open_document_opens_inspection(self):
        record = self._record()
        activity = self._activity(record)
        action = activity.with_user(self.inspector).action_open_document()
        self.assertEqual(action['res_model'], 'qc.inspection')
        self.assertEqual(action['res_id'], record.id)

    # ------------------------------------------------------------------
    # Supervisor rule
    # ------------------------------------------------------------------
    def test_waiting_uses_configured_supervisor_snapshot(self):
        # Category responsible and owner stay on manager, but the explicit
        # equipment supervisor is manager_2: only that configured person owns
        # the "waiting for supervisor" todo (no category/owner inference).
        equipment = self._new_equipment(
            '主管快照设备', inspection_supervisor_id=self.other_manager.id)
        record = self._record(equipment=equipment)
        self.assertEqual(record.inspection_supervisor_id, self.other_manager)
        # Changing the equipment supervisor later must not silently reassign
        # existing inspections/tasks: the snapshot taken at creation is kept.
        equipment.inspection_supervisor_id = self.manager.id
        self.assertEqual(record.inspection_supervisor_id, self.other_manager)
        self._answer(record, failed=True)
        record.action_confirm()
        self.assertEqual(record.state, 'waiting')
        self.assertEqual(self._activity(record).user_id, self.other_manager)

    def test_waiting_without_supervisor_keeps_inspector(self):
        # Simulate a legacy inspection created before the supervisor snapshot
        # existed.  Nobody is guessed: the todo stays on the inspector and both
        # the note and the completion guard explain that configuration is
        # missing.
        record = self._record()
        self.env.cr.execute(
            'UPDATE qc_inspection SET inspection_supervisor_id = NULL WHERE id = %s',
            [record.id])
        record.invalidate_recordset(['inspection_supervisor_id'])
        self.assertFalse(record.inspection_supervisor_id)
        self._activity(record).unlink()
        self._answer(record, failed=True)
        record.action_confirm()
        self.assertEqual(record.state, 'waiting')
        activity = self._activity(record)
        self.assertEqual(activity.activity_type_id, self._waiting_type())
        self.assertEqual(activity.user_id, self.inspector)
        self.assertIn('没有配置检查主管', str(activity.note))
        with self.assertRaises(UserError) as error:
            activity.action_feedback()
        self.assertIn('没有配置检查主管', str(error.exception))

    # ------------------------------------------------------------------
    # Permissions / company boundaries
    # ------------------------------------------------------------------
    def test_permissions_and_company_boundaries(self):
        record = self._record()
        activity = self._activity(record)
        inspector_activity = activity.with_user(self.inspector).search(
            [('inspection_id', '=', record.id)])
        self.assertEqual(inspector_activity, activity)
        with self.assertRaises(AccessError):
            record.with_user(self.other_inspector).read(['name'])
        self.assertFalse(activity.with_user(self.other_inspector).search(
            [('inspection_id', '=', record.id)]))
        other_company = self.env['res.company'].create({'name': '活动测试公司'})
        outsider = new_test_user(
            self.env, login='activity_outsider', groups='inspection.group_user',
            company_id=other_company.id)
        with self.assertRaises(AccessError):
            record.with_user(outsider).read(['name'])
        self.assertFalse(activity.with_user(outsider).search(
            [('inspection_id', '=', record.id)]))

    # ------------------------------------------------------------------
    # Deletion / no dangling activity
    # ------------------------------------------------------------------
    def test_delete_draft_cleans_activity_and_equipment_is_restricted(self):
        record = self._record()
        equipment = record.equipment_id
        equipment_id = equipment.id
        activity_id = self._activity(record).id
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            equipment.unlink()
        self.assertTrue(self.env['maintenance.equipment'].browse(equipment_id).exists())
        record.unlink()
        self.assertFalse(self.env['mail.activity'].browse(activity_id).exists())
        self.assertFalse(self._activity(record))
        equipment.unlink()
        self.assertFalse(self.env['mail.activity'].search([
            ('res_model', '=', 'maintenance.equipment'), ('res_id', '=', equipment_id)]))

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------
    def test_backfill_is_idempotent_and_skips_finished(self):
        record = self._record()
        self.assertEqual(len(self._activity(record)), 1)
        # Simulate a record created before this feature existed.
        self._activity(record).unlink()
        self.assertFalse(self._activity(record))
        inspection_model = self.env['qc.inspection']
        inspection_model._sync_inspection_activities()
        first = self._activity(record)
        self.assertEqual(len(first), 1)
        self.assertEqual(first.user_id, self.inspector)
        inspection_model._sync_inspection_activities()
        second = self._activity(record)
        self.assertEqual(second.ids, first.ids)
        # Finished inspections are never given a new todo.
        finished = self._record()
        self._answer(finished)
        finished.action_confirm()
        self.assertEqual(finished.state, 'success')
        self.assertFalse(self._activity(finished))
        inspection_model._sync_inspection_activities()
        self.assertFalse(self._activity(finished))

    def test_backfill_handles_missing_supervisor(self):
        record = self._record()
        self.env.cr.execute(
            'UPDATE qc_inspection SET inspection_supervisor_id = NULL WHERE id = %s',
            [record.id])
        record.invalidate_recordset(['inspection_supervisor_id'])
        self._activity(record).unlink()
        self.env['qc.inspection']._sync_inspection_activities()
        activity = self._activity(record)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, self.inspector)
        self.assertEqual(activity.activity_type_id, self._management_type())
