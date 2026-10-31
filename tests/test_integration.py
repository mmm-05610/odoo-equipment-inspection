# License AGPL-3.0-or-later.
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, new_test_user
from odoo.addons.base.tests.common import BaseCommon


@tagged('post_install','-at_install')
class TestEquipmentInspection(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.team = cls.env['maintenance.team'].create({'name':'测试团队','company_id':cls.env.company.id})
        cls.category = cls.env['maintenance.equipment.category'].create({'name':'测试水泵','company_id':cls.env.company.id})
        cls.supervisor = new_test_user(cls.env, login='inspection_supervisor', groups='inspection.group_manager', company_id=cls.env.company.id)
        cls.test = cls.env['qc.test'].create({'name':'测试方案','inspection_equipment_use':True,'equipment_category_ids':[Command.set(cls.category.ids)],'test_lines':[
            Command.create({'name':'是否渗漏','type':'qualitative','ql_values':[
                Command.create({'name':'无渗漏','ok':True}),Command.create({'name':'渗漏','ok':False})]}),
            Command.create({'name':'计数','type':'quantitative','min_value':0,'max_value':10,'uom_id':cls.unit.id}),
        ]})
        cls.equipment = cls.env['maintenance.equipment'].create({'name':'测试设备','company_id':cls.env.company.id,
            'maintenance_team_id':cls.team.id,'category_id':cls.category.id,'inspection_test_id':cls.test.id,
            'inspection_supervisor_id':cls.supervisor.id})

    def record(self, **kw):
        return self.env['qc.inspection'].create({'equipment_id':self.equipment.id,**kw})

    def answer(self, record, failed=False):
        record.action_todo()
        for line in record.inspection_lines:
            if line.question_type == 'qualitative':
                line.qualitative_value = line.possible_ql_values.filtered(lambda a:a.ok != failed)[:1]
            elif line.question_type == 'quantitative':
                line.quantitative_value = 5
        record.internal_notes = '检查完成，现场运行平稳。'

    def test_reuses_oca_models_and_generator(self):
        record = self.record()
        self.assertEqual(record._name,'qc.inspection')
        self.assertEqual(record.test,self.test)
        self.assertEqual(record.object_id,self.equipment)
        self.assertEqual(len(record.inspection_lines),2)
        self.assertEqual(record.inspection_lines.mapped('test_line'),self.test.test_lines)
        self.assertNotIn('inspection.order',self.env.registry)
        self.assertNotIn('inspection.template',self.env.registry)

    def test_normal_completion(self):
        record=self.record();self.answer(record);record.action_confirm()
        self.assertEqual(record.state,'success')
        self.assertTrue(all(record.inspection_lines.mapped('inspection_locked')))
        self.assertEqual(set(record.inspection_lines.mapped('inspection_result')), {'pass'})

    def test_abnormal_confirm_repair_idempotent(self):
        record=self.record();self.answer(record,failed=True);record.action_confirm()
        self.assertEqual(record.state,'waiting')
        record.action_approve();self.assertEqual(record.state,'failed')
        first=record.action_create_repair();second=record.action_create_repair()
        self.assertEqual(first['res_id'],second['res_id'])
        self.assertEqual(record.maintenance_request_id.inspection_id,record)
        self.assertEqual(record.maintenance_request_id.equipment_id,self.equipment)

    def test_blank_zero_rejected_then_explicit_zero_accepted(self):
        record=self.record();self.answer(record)
        line=record.inspection_lines.filtered(lambda l:l.question_type=='quantitative')
        line.write({'quantitative_value':0});line.inspection_numeric_answered=False
        with self.assertRaises(ValidationError):record.action_confirm()
        line.inspection_numeric_answered=True
        record.action_confirm();self.assertEqual(record.state,'success')

    def test_relational_link_uses_equipment_form_only_for_equipment_checks(self):
        record = self.record()
        self.answer(record, failed=True)
        record.action_confirm()
        record.action_approve()
        record.action_create_repair()
        source = record.maintenance_request_id.inspection_id
        action = source.with_user(self.supervisor).get_formview_action()
        self.assertEqual(action['res_id'], record.id)
        self.assertEqual(action['views'], [(self.env.ref('inspection.view_inspection_form').id, 'form')])
        generic = self.env['qc.inspection'].create({'test': self.test.id})
        self.assertFalse(generic.equipment_id)
        self.assertNotEqual(generic.get_formview_id(), self.env.ref('inspection.view_inspection_form').id)

    def test_cross_unit_conversion(self):
        record=self.record();self.answer(record)
        line=record.inspection_lines.filtered(lambda l:l.question_type=='quantitative')
        # A half-unit: 10 half-units = 5 standard units, within 0..10.
        half=self.env['uom.uom'].create({'name':'半单位','relative_uom_id':self.unit.id,'relative_factor':0.5})
        line.write({'uom_id':half.id,'quantitative_value':10})
        record.action_confirm();self.assertTrue(record.success)

    def test_incompatible_unit_rejected(self):
        record=self.record();self.answer(record)
        line=record.inspection_lines.filtered(lambda l:l.question_type=='quantitative')
        other=self.env['uom.uom'].create({'name':'无关单位'})
        line.uom_id=other
        with self.assertRaises(ValidationError):record.action_confirm()

    def test_completed_results_frozen(self):
        record=self.record();self.answer(record);record.action_confirm()
        line=record.inspection_lines.filtered(lambda l:l.question_type=='quantitative')
        with self.assertRaises(UserError):line.quantitative_value=50
        with self.assertRaises(UserError):record.action_cancel()
        self.assertTrue(record.success)

    def test_history_standard_and_choice_snapshot(self):
        record=self.record();self.answer(record);record.action_confirm()
        line=record.inspection_lines.filtered(lambda l:l.question_type=='qualitative')
        original=line.inspection_answer_label
        line.qualitative_value.name='新选项名'
        self.test.name='新方案名'
        self.test.test_lines.filtered(lambda q:q.type=='quantitative').max_value=100
        self.assertEqual(line.inspection_answer_label,original)
        self.assertEqual(record.inspection_test_name,'测试方案')
        self.assertEqual(record.inspection_lines.filtered(lambda l:l.question_type=='quantitative').max_value,10)
        self.assertTrue(record.success)

    def test_generation_idempotence_and_cancellation(self):
        model=self.env['qc.inspection']
        a=model.generate_for_equipment(self.equipment,source='batch')
        b=model.generate_for_equipment(self.equipment,source='batch')
        self.assertEqual(a,b)
        a.action_cancel()
        c=model.generate_for_equipment(self.equipment,source='batch')
        self.assertNotEqual(c,a)
        self.assertNotEqual(model.generate_for_equipment(self.equipment),c)

    def test_client_cannot_replace_snapshot(self):
        record=self.record(inspection_lines=[Command.create({'name':'伪造项目'})])
        self.assertNotIn('伪造项目',record.inspection_lines.mapped('name'))
        with self.assertRaises(UserError):record.inspection_lines[0].min_value=-99
        with self.assertRaises(UserError):record.inspection_lines[0].unlink()
        with self.assertRaises(UserError):self.env['qc.inspection.line'].create({'inspection_id':record.id,'name':'追加'})

    def test_state_cannot_skip_execution(self):
        record=self.record()
        with self.assertRaises(ValidationError):record.write({'state':'success'})
        with self.assertRaises(UserError):record.action_confirm()

    def test_inspector_assignment_and_approval(self):
        inspector=new_test_user(self.env,login='inspection_worker',groups='inspection.group_user',company_id=self.env.company.id)
        other=new_test_user(self.env,login='inspection_other',groups='inspection.group_user',company_id=self.env.company.id)
        record=self.record(user=inspector.id)
        self.answer(record,failed=True)
        record.with_user(inspector).action_confirm()
        with self.assertRaises(AccessError):record.with_user(inspector).action_approve()
        with self.assertRaises(AccessError):record.with_user(other).read(['name'])
        with self.assertRaises(AccessError):record.inspection_lines.with_user(other).read(['name'])
        with self.assertRaises(AccessError):record.with_user(inspector).write({'user':other.id})

    def test_company_mismatch(self):
        company=self.env['res.company'].create({'name':'其他公司'})
        test=self.env['qc.test'].create({'name':'其他方案','company_id':company.id,'inspection_equipment_use':True,
            'test_lines':[Command.create({'name':'数值','type':'quantitative','uom_id':self.unit.id})]})
        with self.assertRaises(ValidationError):self.record(test=test.id)

    def test_optional_numeric_unanswered(self):
        self.test.test_lines.filtered(lambda q:q.type=='quantitative').inspection_required=False
        record=self.record();self.answer(record)
        record.inspection_lines.filtered(lambda l:l.question_type=='quantitative').inspection_numeric_answered=False
        record.action_confirm();self.assertEqual(record.state,'success')

    def test_batch_wizard_and_cron(self):
        self.equipment.write({
            'inspection_cycle_enabled': True,
            'inspection_cycle_interval': 1,
            'inspection_cycle_unit': 'days',
            'inspection_cycle_start_date': fields.Date.context_today(self.equipment),
        })
        wizard=self.env['inspection.generate'].create({'equipment_ids':[Command.set(self.equipment.ids)]})
        action=wizard.action_generate()
        self.assertEqual(action['res_model'],'qc.inspection')
        self.env['qc.inspection']._cron_generate_cycle_inspections()
        sources = set(self.equipment.inspection_ids.mapped('inspection_source'))
        self.assertEqual(sources, {'batch', 'cron'})
        self.assertEqual(len(self.equipment.inspection_ids), 2)

    def test_report_html(self):
        record=self.record();self.answer(record);record.action_confirm()
        html,_=self.env['ir.actions.report']._render_qweb_html('inspection.report_equipment_inspection',record.ids)
        self.assertIn(record.name.encode(),html)
        self.assertIn('无渗漏'.encode(),html)
        self.assertIn('检查完成，现场运行平稳。'.encode(),html)

    def test_form_updates_existing_answers(self):
        record=self.record();self.answer(record)
        line=record.inspection_lines.filtered(lambda l:l.question_type=='quantitative')
        record.write({'inspection_lines':[Command.update(line.id, {'quantitative_value':6})], 'internal_notes':'网页填写的检查备注'})
        self.assertEqual(line.quantitative_value,6)
        self.assertEqual(record.internal_notes,'网页填写的检查备注')
        with self.assertRaises(UserError):
            record.write({'inspection_lines':[Command.clear()]})

    def test_optional_qualitative_unanswered(self):
        self.test.test_lines.filtered(lambda q:q.type == 'qualitative').inspection_required=False
        record=self.record();record.action_todo()
        record.inspection_lines.filtered(lambda l:l.question_type == 'quantitative').quantitative_value=5
        record.action_confirm()
        self.assertEqual(record.state,'success')
        self.assertEqual(record.inspection_lines.filtered(lambda l:l.question_type == 'qualitative').inspection_result,'na')

    def test_only_native_question_types(self):
        for model, field in [('qc.test.question','type'),('qc.inspection.line','question_type')]:
            selection=self.env[model]._fields[field]._description_selection(self.env)
            self.assertEqual({value for value, label in selection}, {'qualitative','quantitative'})
        self.assertNotIn('inspection_text',self.env['qc.inspection.line']._fields)
        with self.assertRaises(ValueError):
            self.env['qc.test.question'].create({'name':'不支持的题型','test':self.test.id,'type':'text'})

    def test_category_rejects_wrong_or_missing_category(self):
        other = self.env['maintenance.equipment.category'].create({'name': '风机'})
        for category_id in [False, other.id]:
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.equipment.write({'category_id': category_id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.test.equipment_category_ids = False
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.test.inspection_equipment_use = False

    def test_creation_checks_explicit_scheme_category(self):
        other = self.env['maintenance.equipment.category'].create({'name': '风机'})
        test = self.test.copy({'equipment_category_ids': [Command.set(other.ids)]})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.record(test=test.id)
        self.equipment.inspection_test_id = False
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.equipment.inspection_test_id = test

    def test_many_categories_and_schemes_and_onchange(self):
        other = self.env['maintenance.equipment.category'].create({'name': '风机'})
        self.test.equipment_category_ids = self.category | other
        fan = self.equipment.copy({'category_id': other.id})
        self.env['qc.inspection'].generate_for_equipment(fan)
        second = self.test.copy({'name': '维修后验收'})
        old = self.record()
        self.equipment.inspection_test_id = second
        self.assertEqual(self.record().test, second)
        self.assertEqual(old.test, self.test)
        unmatched = self.env['maintenance.equipment.category'].create({'name': '其他设备'})
        form = self.env['maintenance.equipment'].new({'category_id': unmatched.id, 'inspection_test_id': second.id})
        form._onchange_inspection_category()
        self.assertFalse(form.inspection_test_id)

    def test_blank_categories_cannot_be_assigned(self):
        blank = self.test.copy({'equipment_category_ids': [Command.clear()]})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.equipment.inspection_test_id = blank
