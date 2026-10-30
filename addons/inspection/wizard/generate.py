# License AGPL-3.0-or-later.
from odoo import fields, models, _
from odoo.exceptions import UserError, ValidationError


class InspectionGenerate(models.TransientModel):
    _name = 'inspection.generate'
    _description = '批量生成设备检查'

    mode = fields.Selection(
        [('temporary', '生成临时检查'), ('due', '生成到期检查')],
        string='生成方式', required=True, default='temporary',
        help='临时检查独立于周期，不占用计划期次；到期检查调用与后台任务相同的周期入口。')
    inspection_date = fields.Date('业务日期', default=fields.Date.context_today,
                                  help='仅临时检查使用。')
    equipment_ids = fields.Many2many('maintenance.equipment', string='设备', required=True,
                                     domain="[('inspection_test_id','!=',False)]")
    test_id = fields.Many2one(
        'qc.test', string='临时检查方案',
        domain="[('inspection_equipment_use','=',True)]",
        help='仅临时检查使用；留空时使用所选设备各自的默认方案。')

    def action_generate(self):
        self.ensure_one()
        if not self.equipment_ids:
            raise UserError(_('请选择设备。'))
        if self.mode == 'due':
            return self._generate_due()
        return self._generate_temporary()

    def _generate_due(self):
        Inspection = self.env['qc.inspection']
        today = self.env['maintenance.equipment']._inspection_business_today()
        records = Inspection
        for equipment in self.equipment_ids:
            record = Inspection.generate_cycle_for_equipment(equipment, today=today)
            if record:
                records |= record
        if not records:
            raise UserError(_('所选设备当前没有到期的周期检查。'))
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action['domain'] = [('id', 'in', records.ids)]
        return action

    def _generate_temporary(self):
        if not self.inspection_date:
            raise ValidationError(_('生成临时检查时请填写业务日期。'))
        records = self.env['qc.inspection']
        for equipment in self.equipment_ids:
            if self.test_id:
                self.test_id._check_equipment_compatibility(equipment)
            records |= records.generate_for_equipment(
                equipment, self.inspection_date, source='batch', test=self.test_id or None)
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action['domain'] = [('id', 'in', records.ids)]
        return action
