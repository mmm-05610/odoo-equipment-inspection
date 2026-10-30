# License AGPL-3.0-or-later. Equipment bridge over OCA quality_control_oca.
import logging

from markupsafe import Markup, escape

from odoo import api, fields, models, Command, _
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

TERMINAL = ('success', 'failed', 'canceled')
GENERATION_SOURCES = ('manual', 'batch', 'cron')


class QcInspection(models.Model):
    _inherit = 'qc.inspection'
    _check_company_auto = True

    state = fields.Selection(selection_add=[('plan','计划中'),('draft','草稿'),('ready','待检查'),
        ('waiting','待主管确认'),('success','合格'),('failed','异常'),('canceled','已取消')])

    equipment_id = fields.Many2one('maintenance.equipment', string='设备', index=True,
                                  ondelete='restrict', check_company=True)
    equipment_category_id = fields.Many2one(related='equipment_id.category_id', string='设备类别')
    inspection_date = fields.Date('业务日期', default=fields.Date.context_today, index=True)
    inspection_source = fields.Selection([('manual', '手动'), ('batch', '批量'), ('cron', '周期任务')],
                                         string='生成来源', default='manual', required=True)
    inspection_cycle_date = fields.Date(
        '周期日期', readonly=True, copy=False, index=True,
        help='由周期任务生成时记录的计划日期；一次性检查为空。用于周期去重和补建，不参与手动检查。')
    inspection_supervisor_id = fields.Many2one(
        'res.users', '当次检查主管', readonly=True, copy=False, check_company=True,
        help='创建时从设备复制的主管，用于“待主管确认”活动；保留当次负责人，'
             '之后修改设备主管不会改派已有检查。')
    maintenance_request_id = fields.Many2one('maintenance.request', string='维修请求',
                                           copy=False, ondelete='restrict', check_company=True)
    inspection_test_name = fields.Char('当次方案名称', readonly=True)
    inspection_conclusion = fields.Selection([('pending', '待完成'), ('pass', '合格'), ('fail', '异常')],
                                            string='检查结论', compute='_compute_inspection_conclusion')

    # Batch entries are de-duplicated among themselves (double submit of the
    # same temporary inspection), never against a cycle occurrence: a temporary
    # inspection for another scheme may legitimately coexist with the plan.
    _equipment_batch_unique = models.UniqueIndex(
        "(equipment_id, inspection_date, test) WHERE equipment_id IS NOT NULL "
        "AND inspection_source = 'batch' AND state != 'canceled'"
    )
    # One active inspection per plan occurrence.  This is the cycle identity
    # (equipment + planned date) and the database guard behind the row lock.
    _equipment_cycle_date_unique = models.UniqueIndex(
        "(equipment_id, inspection_cycle_date) WHERE equipment_id IS NOT NULL "
        "AND inspection_cycle_date IS NOT NULL AND state != 'canceled'"
    )
    _repair_unique = models.Constraint('unique(maintenance_request_id)', '维修请求不能重复关联检查记录。')

    def object_selection_values(self):
        return super().object_selection_values() + [('maintenance.equipment', '设备')]

    @api.depends('state', 'success')
    def _compute_inspection_conclusion(self):
        for record in self:
            record.inspection_conclusion = ('pass' if record.success else 'fail') if record.state in ('success','failed','waiting') else 'pending'

    @api.onchange('equipment_id')
    def _onchange_equipment_id(self):
        if self.equipment_id:
            self.test = self.equipment_id.inspection_test_id
            self.user = self.equipment_id.technician_user_id or self.env.user

    @api.model_create_multi
    def create(self, vals_list):
        values = []
        for original in vals_list:
            vals = dict(original)
            if vals.get('equipment_id'):
                equipment = self.env['maintenance.equipment'].browse(vals['equipment_id']).exists()
                equipment.check_access('read')
                if not equipment or equipment.company_id not in self.env.companies:
                    raise AccessError(_('设备不属于当前允许的公司。'))
                test = self.env['qc.test'].browse(vals.get('test') or equipment.inspection_test_id.id).exists()
                test.check_access('read')
                if not test or not test.inspection_equipment_use or not test.test_lines:
                    raise ValidationError(_('请选择包含检查项目的设备检查方案。'))
                if test.company_id and test.company_id != equipment.company_id:
                    raise ValidationError(_('设备与检查方案必须属于同一公司。'))
                test._check_equipment_compatibility(equipment)
                if vals.get('state', 'draft') != 'draft':
                    raise ValidationError(_('检查记录必须从草稿开始。'))
                if vals.get('maintenance_request_id'):
                    raise ValidationError(_('请通过异常转维修操作关联维修请求。'))
                supervisor = equipment.inspection_supervisor_id
                if not supervisor:
                    raise ValidationError(_(
                        '设备“%s”还没有配置检查主管，请先在设备“检查”页签选择检查主管。',
                        equipment.display_name))
                assigned = vals.get('user') or equipment.technician_user_id.id or self.env.uid
                if not self.env.su and not self.env.user.has_group('inspection.group_manager') and assigned != self.env.uid:
                    raise AccessError(_('检查员只能创建分配给自己的任务。'))
                vals.update(equipment_id=equipment.id, test=test.id, company_id=equipment.company_id.id,
                            object_id=f'maintenance.equipment,{equipment.id}', user=assigned,
                            inspection_supervisor_id=supervisor.id,
                            inspection_test_name=test.name, state='draft')
                vals.setdefault('inspection_date', fields.Date.context_today(self))
                # OCA supplies content generation; the bridge only adds frozen answer metadata.
                lines = []
                for question in test.test_lines:
                    line = self._prepare_inspection_line(test, question, fill=False)
                    line.update(inspection_required=question.inspection_required,
                                inspection_answers={str(a.id): {'name': a.name, 'ok': a.ok} for a in question.ql_values},
                                inspection_unit_name=question.uom_id.display_name or '')
                    lines.append(Command.create(line))
                vals['inspection_lines'] = lines
            values.append(vals)
        return super().create(values)

    def _equipment_records(self):
        return self.filtered('equipment_id')

    def _check_answers(self):
        for record in self._equipment_records():
            if not record.inspection_lines:
                raise ValidationError(_('检查记录不能为空。'))
            for line in record.inspection_lines:
                if line.inspection_required and not line.inspection_answered:
                    raise ValidationError(_('请填写检查项目：%s', line.name))
                if line.question_type == 'quantitative' and line.inspection_answered:
                    if not line.uom_id or not line.test_uom_id or not line.uom_id._has_common_reference(line.test_uom_id):
                        raise ValidationError(_('实际单位必须与标准单位属于同一计量体系。'))

    def write(self, vals):
        equipment_records = self._equipment_records()
        if vals.get('equipment_id') and len(equipment_records) != len(self):
            raise UserError(_('设备检查必须直接从设备创建。'))
        fixed = {'equipment_id','object_id','test','company_id','inspection_date','inspection_source',
                 'inspection_cycle_date','inspection_supervisor_id','inspection_test_name'}
        if equipment_records and 'inspection_lines' in vals:
            allowed_ids = set(equipment_records.inspection_lines.ids)
            if any(cmd[0] != Command.UPDATE or cmd[1] not in allowed_ids for cmd in vals['inspection_lines']):
                raise UserError(_('只能填写已有检查项目的结果。'))
        if equipment_records and fixed.intersection(vals):
            raise UserError(_('检查对象和标准快照创建后不能修改，请取消后重新创建。'))
        if equipment_records and 'maintenance_request_id' in vals:
            raise UserError(_('维修关联只能通过异常转维修操作建立。'))
        if equipment_records and 'user' in vals and not self.env.su and not self.env.user.has_group('inspection.group_manager'):
            raise AccessError(_('只有检查主管可以重新分配任务。'))
        if equipment_records.filtered(lambda r: r.state in TERMINAL) and set(vals).intersection({'state','user','date','qty','external_notes','internal_notes'}):
            raise UserError(_('已结束的检查记录不能修改。'))
        if 'state' in vals:
            allowed = {'draft':{'ready','canceled'}, 'ready':{'success','waiting','canceled'}, 'waiting':{'success','failed','canceled'}}
            for record in equipment_records:
                state = vals['state']
                if state not in allowed.get(record.state,set()):
                    raise ValidationError(_('不允许的检查状态转换。'))
                if record.state == 'waiting' and state in ('success','failed') and not self.env.su and not self.env.user.has_group('inspection.group_manager'):
                    raise AccessError(_('异常检查需要检查主管确认。'))
                if state in ('success','failed','waiting'):
                    record._check_answers()
                    if (state == 'success') != bool(record.success):
                        raise ValidationError(_('状态与实际判定结果不一致。'))
                if state in ('success','failed'):
                    record.inspection_lines._lock_equipment_results()
        return super().write(vals)

    def action_todo(self):
        if any(r.state != 'draft' for r in self._equipment_records()):
            raise UserError(_('只有草稿可以开始检查。'))
        return super().action_todo()

    def action_confirm(self):
        for record in self:
            if not record.equipment_id:
                super(QcInspection, record).action_confirm()
                continue
            if record.state != 'ready':
                raise UserError(_('请先开始检查，再提交结果。'))
            record._check_answers()
            if any(not line.inspection_required and not line.inspection_answered
                   for line in record.inspection_lines):
                # OCA requires every qualitative answer; allow explicitly optional unanswered items.
                if record.success:
                    super(QcInspection, record).action_approve()
                else:
                    record.state = 'waiting'
            else:
                super(QcInspection, record).action_confirm()
        return True

    def action_approve(self):
        if any(r.state != 'waiting' for r in self._equipment_records()):
            raise UserError(_('只有待确认的异常记录需要主管确认。'))
        return super().action_approve()

    @api.ondelete(at_uninstall=False)
    def _unlink_equipment_inspections(self):
        if self._equipment_records() and not self.env.su and not self.env.user.has_group('inspection.group_manager'):
            raise AccessError(_('检查员不能删除检查记录。'))

    # ------------------------------------------------------------------
    # Exception -> maintenance request
    # ------------------------------------------------------------------
    def _inspection_failed_lines(self):
        self.ensure_one()
        lines = self.inspection_lines.filtered(lambda line: line.inspection_result == 'fail')
        if not lines:
            lines = self.inspection_lines.filtered(
                lambda line: line.inspection_answered and not line.success)
        return lines

    def _repair_request_title(self):
        self.ensure_one()
        lines = self._inspection_failed_lines()
        if len(lines) == 1:
            detail = lines[0].name
        elif lines:
            detail = _('%(first)s 等 %(count)s 项', first=lines[0].name, count=len(lines))
        else:
            detail = _('检查异常')
        return _('%(equipment)s：%(detail)s异常待处理',
                 equipment=self.equipment_id.name, detail=detail)[:120]

    def _repair_request_description(self):
        """Describe only the frozen observations of this inspection.

        All standards, answers and units come from the snapshot stored on the
        inspection lines, never from the current scheme definition.
        """
        self.ensure_one()
        lines = self._inspection_failed_lines()
        items = Markup()
        for line in lines:
            if line.question_type == 'qualitative':
                detail = _('选择“%(answer)s”，判定异常',
                           answer=line.inspection_answer_label or _('未填写'))
            else:
                detail = _('实测 %(value)s %(uom)s，标准 %(min)s～%(max)s %(standard_uom)s，判定异常') % {
                    'value': line.quantitative_value,
                    'uom': line.uom_id.display_name or '',
                    'min': line.min_value,
                    'max': line.max_value,
                    'standard_uom': line.inspection_unit_name or '',
                }
            items += Markup('<li><strong>%s</strong>：%s</li>') % (escape(line.name), detail)
        if not items:
            items = Markup('<li>%s</li>') % escape(_('检查结论为异常。'))
        action = self.env.ref('inspection.action_inspections', raise_if_not_found=False)
        link = ''
        if action:
            link = Markup('　<a href="/odoo/action-%s/%s">%s</a>') % (
                action.id, self.id, escape(_('打开检查记录')))
        notes = self.internal_notes or ''
        description = Markup('<p>%s</p>') % _(
            '检查编号：%(number)s　方案：%(test)s　业务日期：%(date)s') % {
                'number': escape(self.name),
                'test': escape(self.inspection_test_name or ''),
                'date': escape(fields.Date.to_string(self.inspection_date)),
            }
        description += Markup('<ul>%s</ul>') % items
        if notes:
            description += Markup('<p><strong>%s</strong><br/>%s</p>') % (
                escape(_('检查备注')), escape(notes).replace('\n', Markup('<br/>')))
        description += Markup('<p>%s%s</p>') % (escape(_('来源检查编号：%s', self.name)), link)
        return description

    def action_create_repair(self):
        self.ensure_one()
        self.check_access('write')
        if not self.equipment_id or self.state != 'failed':
            raise UserError(_('只有主管确认后的异常设备检查可以转维修。'))
        self.env.cr.execute('SELECT id FROM qc_inspection WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['maintenance_request_id'])
        if not self.maintenance_request_id:
            request = self.env['maintenance.request'].create({
                'name': self._repair_request_title(),
                'equipment_id': self.equipment_id.id,
                'maintenance_team_id': self.equipment_id.maintenance_team_id.id,
                'company_id': self.company_id.id,
                'inspection_id': self.id,
                'description': self._repair_request_description(),
            })
            super(QcInspection, self).write({'maintenance_request_id': request.id})
        return {'type':'ir.actions.act_window','res_model':'maintenance.request',
                'res_id':self.maintenance_request_id.id,'view_mode':'form','target':'current'}

    @api.readonly
    def get_formview_id(self, access_uid=None):
        # Relational links (e.g. a repair's source inspection) use this native
        # hook, independently of the menu action that originally opened us.
        if len(self) == 1 and self.equipment_id:
            return self.env.ref('inspection.view_inspection_form').id
        return super().get_formview_id(access_uid=access_uid)

    def action_open_form(self):
        self.ensure_one()
        self.check_access('read')
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action.update(res_id=self.id,
                      views=[(self.env.ref('inspection.view_inspection_form').id, 'form')])
        return action

    def action_rebuild_cycle_inspection(self):
        """Rebuild one canceled cycle occurrence without moving the schedule.

        The rule is explicit: canceling a cycle inspection does not rewind the
        next planned date and the scheduler never rebuilds it automatically.
        A supervisor may rebuild that occurrence from the canceled record.
        """
        self.ensure_one()
        if not self.env.user.has_group('inspection.group_manager'):
            raise AccessError(_('只有检查主管可以补建周期检查。'))
        if self.state != 'canceled' or not self.inspection_cycle_date:
            raise UserError(_('只有已取消的周期检查可以补建。'))
        equipment = self.equipment_id
        if not equipment.inspection_cycle_enabled or equipment.inspection_cycle_paused:
            raise UserError(_('设备未启用周期检查或已暂停，不能补建。'))
        record = self.env['qc.inspection'].generate_for_equipment(
            equipment, self.inspection_cycle_date, source='cron',
            cycle_date=self.inspection_cycle_date)
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action.update(res_id=record.id,
                      views=[(self.env.ref('inspection.view_inspection_form').id, 'form')])
        return action

    # ------------------------------------------------------------------
    # Generation (single creation path for manual / batch / cycle entries)
    # ------------------------------------------------------------------
    @api.model
    def generate_for_equipment(self, equipment, date=None, source='manual',
                               cycle_date=None, test=None):
        if source not in GENERATION_SOURCES:
            raise ValidationError(_('未知生成来源。'))
        equipment.ensure_one()
        test_id = (test or equipment.inspection_test_id).id
        if cycle_date and source != 'cron':
            raise ValidationError(_('只有周期任务可以写入周期日期。'))
        equipment.check_access('read')
        date = fields.Date.to_date(date) if date else equipment._inspection_business_today()
        cycle_date = fields.Date.to_date(cycle_date) if cycle_date else False
        if cycle_date and cycle_date != date:
            raise ValidationError(_('周期日期必须与检查业务日期一致。'))
        if source != 'manual':
            self.env.cr.execute(
                'SELECT id FROM maintenance_equipment WHERE id = %s FOR UPDATE', [equipment.id])
            # The row lock serialises concurrent schedulers and batch entries;
            # the lookup is global, the returned record still enforces access.
            equipment.invalidate_recordset()
            if source == 'batch':
                # Only batch against batch: a temporary inspection does not
                # consume a plan occurrence and may coexist with the cycle.
                existing = self.sudo().search([
                    ('equipment_id', '=', equipment.id),
                    ('inspection_date', '=', date),
                    ('test', '=', test_id),
                    ('inspection_source', '=', 'batch'),
                    ('state', '!=', 'canceled'),
                ], limit=1)
            else:
                # Cycle identity is the planned date, not "any inspection on
                # that day": another scheme or a temporary check is not a
                # reason to consider this occurrence done.
                existing = self.sudo().search([
                    ('equipment_id', '=', equipment.id),
                    ('inspection_cycle_date', '=', cycle_date),
                    ('state', '!=', 'canceled'),
                ], limit=1) if cycle_date else self.browse()
            if existing:
                existing = self.browse(existing.id)
                existing.check_access('read')
                return existing
        values = {'equipment_id': equipment.id, 'inspection_date': date,
                  'inspection_source': source}
        if cycle_date:
            values['inspection_cycle_date'] = cycle_date
        if test:
            values['test'] = test.id
        return self.create(values)

    @api.model
    def _cycle_due_date(self, equipment, today):
        """Latest due plan date for ``equipment`` on ``today`` (or None)."""
        index = equipment._inspection_cycle_index_on_or_before(today)
        if index is None:
            return None
        return equipment._inspection_cycle_occurrence(index)

    @api.model
    def generate_cycle_for_equipment(self, equipment, today=None):
        """Generate at most the latest due cycle of one device.

        This is the only place that advances ``inspection_cycle_next_date``.
        The next date is written *after* a successful creation, so a failed
        creation never skips an occurrence.  Missed periods are skipped on
        purpose: a stopped device produces one catch-up inspection, not a burst.
        """
        equipment.ensure_one()
        if not equipment.inspection_cycle_enabled or equipment.inspection_cycle_paused:
            return self.browse()
        equipment.check_access('read')
        # Re-read the schedule under the equipment row lock so concurrent
        # scheduler threads cannot generate the same occurrence twice.
        self.env.cr.execute(
            'SELECT id FROM maintenance_equipment WHERE id = %s FOR UPDATE', [equipment.id])
        equipment.invalidate_recordset()
        if not equipment.inspection_cycle_enabled or equipment.inspection_cycle_paused:
            return self.browse()
        today = fields.Date.to_date(today) if today else equipment._inspection_business_today()
        if not equipment.inspection_cycle_next_date:
            equipment._inspection_cycle_recompute_next_date(today)
        next_date = equipment.inspection_cycle_next_date
        if not next_date or next_date > today:
            return self.browse()
        due_date = self._cycle_due_date(equipment, today)
        if not due_date:
            return self.browse()
        if due_date < next_date:
            due_date = next_date
        record = self.generate_for_equipment(
            equipment, date=due_date, source='cron', cycle_date=due_date)
        future_date = equipment._inspection_cycle_first_occurrence_after(today)
        equipment.sudo().write({
            'inspection_cycle_next_date': future_date,
            'inspection_cycle_last_error': False,
        })
        return record

    @api.model
    def _cron_generate_cycle_inspections(self):
        """Daily scheduler entry point.

        One job handles all devices; failures are isolated per device (savepoint)
        so one bad device cannot block the others and its next date is not moved.
        """
        today = self.env['maintenance.equipment']._inspection_business_today()
        equipments = self.env['maintenance.equipment'].search([
            ('inspection_cycle_enabled', '=', True),
            ('inspection_cycle_paused', '=', False),
        ])
        total = len(equipments)
        if not total:
            return 0
        # Odoo 19 cron progress API: process bounded batches and report progress
        # so a large fleet is split over several scheduler runs instead of one
        # long transaction.  Outside a cron job (tests, shell) it stays a plain
        # loop without committing the caller's transaction.
        batch_size = 50
        generated = 0
        processed = 0
        for start in range(0, total, batch_size):
            batch = equipments[start:start + batch_size]
            for equipment in batch:
                try:
                    with self.env.cr.savepoint():
                        if self.generate_cycle_for_equipment(equipment, today=today):
                            generated += 1
                except Exception as error:  # noqa: BLE001 - reported on the device
                    _logger.warning(
                        'Cycle inspection generation failed for equipment %s',
                        equipment.id, exc_info=True)
                    equipment.invalidate_recordset()
                    equipment.sudo().write({
                        'inspection_cycle_last_error': str(error)[:500] or _('未知错误'),
                    })
            processed += len(batch)
            if self.env.context.get('ir_cron_progress_id'):
                seconds = self.env['ir.cron']._commit_progress(
                    processed=len(batch), remaining=total - processed)
                if seconds <= 0:
                    _logger.info('Cycle scheduler time budget reached after %s devices', processed)
                    break
        _logger.info('Cycle inspection scheduler generated %s inspection(s)', generated)
        return generated
