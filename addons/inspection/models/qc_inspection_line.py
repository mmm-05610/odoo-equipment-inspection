# License AGPL-3.0-or-later. Narrow extensions to OCA result lines.
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class QcInspectionLine(models.Model):
    _inherit = 'qc.inspection.line'

    inspection_required = fields.Boolean('必须回答', default=True)
    inspection_answers = fields.Json('当次答案标准', readonly=True)
    inspection_answer_label = fields.Char('当次所选答案', readonly=True)
    inspection_unit_name = fields.Char('当次标准单位', readonly=True)
    inspection_numeric_answered = fields.Boolean('已测量', help='数值为 0 时也需要勾选；修改数值时自动勾选。')
    inspection_answered = fields.Boolean('已回答', compute='_compute_answered')
    inspection_locked = fields.Boolean('结果已冻结', readonly=True, copy=False)
    inspection_result = fields.Selection([('pass','合格'),('fail','异常'),('na','仅记录')], string='最终判定', readonly=True, copy=False)

    @api.depends('question_type','qualitative_value','inspection_numeric_answered')
    def _compute_answered(self):
        for line in self:
            line.inspection_answered = (bool(line.qualitative_value) if line.question_type == 'qualitative'
                else line.inspection_numeric_answered)

    @api.depends('question_type','uom_id','test_uom_id','max_value','min_value','quantitative_value',
                 'qualitative_value','possible_ql_values','inspection_answers','inspection_locked',
                 'inspection_result','inspection_numeric_answered','inspection_required')
    def _compute_quality_test_check(self):
        upstream = self.browse()
        for line in self:
            if not line.inspection_id.equipment_id:
                upstream |= line
            elif line.inspection_locked:
                line.success = line.inspection_result != 'fail'
            elif not line.inspection_required and not line.inspection_answered:
                line.success = True
            elif line.question_type == 'qualitative':
                answer = (line.inspection_answers or {}).get(str(line.qualitative_value.id), {})
                line.success = bool(answer.get('ok'))
            elif not line.inspection_numeric_answered:
                line.success = False
            elif line.uom_id != line.test_uom_id:
                # Fixed-version upstream calls conversion on an empty model with an integer id.
                if line.uom_id and line.test_uom_id and line.uom_id._has_common_reference(line.test_uom_id):
                    amount = line.uom_id._compute_quantity(line.quantitative_value, line.test_uom_id, round=False)
                    line.success = line.min_value <= amount <= line.max_value
                else:
                    line.success = False
            else:
                upstream |= line  # ordinary numeric judgment remains OCA's implementation
        if upstream:
            super(QcInspectionLine, upstream)._compute_quality_test_check()

    def _equipment_lines(self):
        return self.filtered(lambda line: line.inspection_id.equipment_id)

    def write(self, vals):
        equipment_lines = self._equipment_lines()
        if equipment_lines:
            if any(line.inspection_id.state not in ('draft','ready') for line in equipment_lines):
                raise UserError(_('已提交的检查结果不能修改。'))
            immutable = {'name','test_line','inspection_id','possible_ql_values','question_type','min_value',
                         'max_value','test_uom_id','inspection_required','inspection_answers',
                         'inspection_answer_label','inspection_unit_name','inspection_locked','inspection_result','success'}
            if immutable.intersection(vals):
                raise UserError(_('不能修改当次检查标准或直接写入判定结果。'))
            vals = dict(vals)
            if 'quantitative_value' in vals:
                vals['inspection_numeric_answered'] = True
            if 'qualitative_value' in vals:
                for line in equipment_lines:
                    answer = (line.inspection_answers or {}).get(str(vals['qualitative_value']))
                    if vals['qualitative_value'] and answer is None:
                        raise ValidationError(_('所选答案不属于当次检查标准。'))
                    super(QcInspectionLine, line).write({**vals,'inspection_answer_label':answer['name'] if answer else ''})
                return super(QcInspectionLine, self - equipment_lines).write(vals) if self - equipment_lines else True
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            parent = self.env['qc.inspection'].browse(vals.get('inspection_id'))
            if parent.equipment_id and parent.inspection_lines:
                raise UserError(_('设备检查项目由方案生成，不能追加结果行。'))
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_equipment_lines(self):
        if self._equipment_lines():
            raise UserError(_('不能单独删除设备检查项目；可删除整张草稿检查记录。'))

    def _lock_equipment_results(self):
        for line in self._equipment_lines():
            result = 'na' if not line.inspection_answered else ('pass' if line.success else 'fail')
            super(QcInspectionLine, line).write({'inspection_result':result,'inspection_locked':True})
