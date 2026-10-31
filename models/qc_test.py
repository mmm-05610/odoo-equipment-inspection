# License AGPL-3.0-or-later. Local extensions; upstream OCA remains unchanged.
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class QcTest(models.Model):
    _inherit = 'qc.test'

    _check_company_auto = True

    inspection_equipment_use = fields.Boolean('用于设备检查', default=False)
    equipment_category_ids = fields.Many2many(
        'maintenance.equipment.category', 'inspection_test_equipment_category_rel',
        'test_id', 'category_id', string='适用设备类别', check_company=True,
        help='至少选择一个类别后，才能分配给设备。空白不表示适用于全部设备。',
    )
    inspection_instructions = fields.Text('使用说明', help='说明使用场景、检查前提和注意事项；不自动安排执行周期。')

    def _check_equipment_compatibility(self, equipment):
        self.ensure_one()
        equipment.ensure_one()
        if not self.inspection_equipment_use or not equipment.category_id or equipment.category_id not in self.equipment_category_ids:
            raise ValidationError(_('方案“%s”不适用于设备“%s”的类别，请重新选择检查方案。', self.display_name, equipment.display_name))

    @api.constrains('equipment_category_ids', 'inspection_equipment_use', 'company_id')
    def _check_assigned_equipment(self):
        # Include all assigned equipment, even when the editing user cannot see it.
        # Historical inspections intentionally keep their original standards.
        for test in self:
            equipment = self.env['maintenance.equipment'].sudo().with_context(active_test=False).search([
                ('inspection_test_id', '=', test.id),
            ])
            for record in equipment:
                test.sudo()._check_equipment_compatibility(record)
                if test.company_id and test.company_id != record.company_id:
                    raise ValidationError(_('方案与已分配设备必须属于同一公司。'))


class QcTestQuestion(models.Model):
    _inherit = 'qc.test.question'

    inspection_required = fields.Boolean('必须回答', default=True)

    @api.constrains('type', 'uom_id', 'test')
    def _check_equipment_unit(self):
        for question in self:
            if question.test.inspection_equipment_use and question.type == 'quantitative' and not question.uom_id:
                raise ValidationError(_('数值检查项必须选择计量单位。'))
