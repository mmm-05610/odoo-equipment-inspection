# License AGPL-3.0-or-later.
"""Equipment-side configuration of periodic inspections.

Design note (keep this small and explicit):

* ``ir.cron`` only wakes the scheduler once a day; it is not the business cycle.
* The business cycle lives on ``maintenance.equipment``: enabled, interval,
  unit, anchor date, paused flag and the next planned date.
* Planned dates are always computed from the anchor with ``relativedelta``
  (days / weeks / months).  Monthly plans therefore keep the original anchor:
  2026-01-31 -> 2026-02-28 -> 2026-03-31, never drifting.
* Actual inspection creation stays in ``qc.inspection``; this file never
  creates inspections and never copies the OCA inspection workflow.
"""
import logging
from datetime import datetime

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

CYCLE_UNITS = ('days', 'weeks', 'months')
# Fields a inspection supervisor may change and that require a new next date.
CYCLE_SCHEDULE_FIELDS = (
    'inspection_cycle_enabled',
    'inspection_cycle_interval',
    'inspection_cycle_unit',
    'inspection_cycle_start_date',
)
# All cycle fields reserved to inspection supervisors.  The database ACL already
# prevents inspectors from writing equipment; this guard keeps the intent clear
# and covers code paths that would otherwise bypass the form.
CYCLE_EDIT_FIELDS = CYCLE_SCHEDULE_FIELDS + (
    'inspection_cycle_paused',
    'inspection_cycle_next_date',
)


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    inspection_test_id = fields.Many2one(
        'qc.test', string='检查方案', check_company=True,
        domain="[('inspection_equipment_use','=',True), ('equipment_category_ids','in',[category_id])]",
        ondelete='restrict')
    inspection_ids = fields.One2many('qc.inspection', 'equipment_id', string='检查历史')
    inspection_count = fields.Integer('检查次数', compute='_compute_inspection_count')
    inspection_last_date = fields.Date('最近完成日期', compute='_compute_inspection_last_date', store=True)

    # Read-only summary for the equipment form.  Records are searched with the
    # current user's rules (never sudo), so an inspector only sees his records.
    inspection_last_completed_id = fields.Many2one(
        'qc.inspection', '最近完成的检查', compute='_compute_inspection_last_completed')
    inspection_last_completed_state = fields.Selection(
        related='inspection_last_completed_id.state', string='最近检查状态')
    inspection_last_completed_test = fields.Char(
        '最近检查方案', compute='_compute_inspection_last_completed')
    inspection_waiting_count = fields.Integer(
        '待主管确认', compute='_compute_inspection_state_counts')
    inspection_ready_count = fields.Integer(
        '待检查', compute='_compute_inspection_state_counts')
    inspection_recent_ids = fields.One2many(
        'qc.inspection', compute='_compute_inspection_recent_ids',
        string='最近检查记录')
    inspection_cycle_is_due = fields.Boolean(
        '周期已到期', compute='_compute_inspection_cycle_is_due')

    # Business cycle: independent from the scheme purpose, the equipment
    # category and the maintenance request workflow.
    inspection_cycle_enabled = fields.Boolean(
        '启用周期检查', default=False, copy=False,
        help='启用后由“设备检查：按周期生成”计划任务在到期日生成检查记录。')
    inspection_cycle_active = fields.Boolean(
        '启用周期检查', compute='_compute_inspection_cycle_active',
        inverse='_inverse_inspection_cycle_active',
        help='关闭即暂停，保留周期配置和计划进度；重新开启后最多补最近一个已到期周期。')
    inspection_cycle_interval = fields.Integer(
        '每隔', default=1, copy=False,
        help='必须为正整数；配合右侧周期单位使用。')
    inspection_cycle_unit = fields.Selection(
        [('days', '天'), ('weeks', '周'), ('months', '月')],
        string='周期单位', default='days', required=True, copy=False,
        help='周按 7 天计算；月按日历月计算，月底缺日时取当月最后一天。')
    inspection_cycle_start_date = fields.Date(
        '首次计划日期', copy=False,
        help='周期锚点。所有计划日期都从这一天按固定周期推算，不受实际执行日期影响。')
    inspection_cycle_next_date = fields.Date(
        '下次计划日期', readonly=True, copy=False, index=True,
        help='最近一个尚未生成的计划日期；到期后由计划任务生成检查并推进到下一期。')
    inspection_cycle_paused = fields.Boolean(
        '暂停周期检查', default=False, copy=False,
        help='暂停期间计划任务不会生成检查；恢复后最多补最近一个已到期周期。')
    inspection_cycle_last_error = fields.Text(
        '最近一次自动生成失败原因', readonly=True, copy=False)
    inspection_supervisor_id = fields.Many2one(
        'res.users', '检查主管', check_company=True,
        help='负责确认本设备异常检查的主管，必须属于“检查主管”角色；'
             '新检查会把当次主管保存到检查单，之后修改设备主管不会改派已有任务。')
    inspection_cycle_can_edit = fields.Boolean(
        '可配置周期检查', compute='_compute_inspection_cycle_can_edit',
        help='仅用于界面只读控制；真正的写入权限由记录规则和 write() 共同保证。')

    def _inspection_form_action(self, record):
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action.update(
            res_id=record.id,
            views=[(self.env.ref('inspection.view_inspection_form').id, 'form')],
        )
        return action

    @api.depends_context('uid')
    def _compute_inspection_cycle_can_edit(self):
        can_edit = self.env.user.has_group('inspection.group_manager')
        for equipment in self:
            equipment.inspection_cycle_can_edit = can_edit

    @api.onchange('category_id')
    def _onchange_inspection_category(self):
        if self.inspection_test_id and self.category_id not in self.inspection_test_id.equipment_category_ids:
            self.inspection_test_id = False

    @api.constrains('category_id', 'inspection_test_id')
    def _check_inspection_category(self):
        for equipment in self.filtered('inspection_test_id'):
            equipment.inspection_test_id._check_equipment_compatibility(equipment)

    @api.depends('inspection_ids')
    def _compute_inspection_count(self):
        for equipment in self:
            equipment.inspection_count = len(equipment.inspection_ids)

    @api.depends('inspection_ids.state', 'inspection_ids.date_done', 'inspection_ids.inspection_date')
    def _compute_inspection_last_date(self):
        for equipment in self:
            completed = equipment.inspection_ids.filtered(
                lambda record: record.state in ('success', 'failed'))
            last = max(completed, key=lambda record: (
                record.date_done or fields.Datetime.to_datetime(record.inspection_date),
                record.id), default=None)
            if last and last.date_done:
                equipment.inspection_last_date = last.date_done.date()
            else:
                equipment.inspection_last_date = last.inspection_date if last else False

    @api.depends('inspection_ids.state', 'inspection_ids.date_done', 'inspection_ids.inspection_date')
    def _compute_inspection_last_completed(self):
        for equipment in self:
            completed = equipment.inspection_ids.filtered(
                lambda record: record.state in ('success', 'failed'))
            last = max(completed, key=lambda record: (
                record.date_done or fields.Datetime.to_datetime(record.inspection_date),
                record.id), default=None)
            equipment.inspection_last_completed_id = last
            equipment.inspection_last_completed_test = last.inspection_test_name if last else False

    @api.depends('inspection_ids.state')
    def _compute_inspection_state_counts(self):
        for equipment in self:
            states = equipment.inspection_ids.mapped('state')
            equipment.inspection_waiting_count = states.count('waiting')
            equipment.inspection_ready_count = states.count('draft') + states.count('ready')

    @api.depends('inspection_ids')
    def _compute_inspection_recent_ids(self):
        for equipment in self:
            equipment.inspection_recent_ids = self.env['qc.inspection'].search([
                ('equipment_id', '=', equipment.id),
            ], order='inspection_date desc, id desc', limit=5)

    @api.model
    def _inspection_business_today(self):
        """Stable business date.

        The device plan must not change when a user browses from another
        timezone, so the scheduler and the schedule math use one explicit
        business timezone (default Asia/Shanghai for the learning project).
        """
        tz_name = self.env['ir.config_parameter'].sudo().get_param(
            'inspection.business_timezone', 'Asia/Shanghai')
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.UTC
        return datetime.now(pytz.UTC).astimezone(tz).date()

    @api.depends('inspection_cycle_enabled', 'inspection_cycle_paused')
    def _compute_inspection_cycle_active(self):
        for equipment in self:
            equipment.inspection_cycle_active = (
                equipment.inspection_cycle_enabled and not equipment.inspection_cycle_paused)

    def _inverse_inspection_cycle_active(self):
        # One user-facing switch; reuse pause/resume so turning it off does not
        # discard the next occurrence or reset an overdue schedule on resume.
        for equipment in self:
            active = equipment.inspection_cycle_active
            values = {'inspection_cycle_paused': not active}
            if active and not equipment.inspection_cycle_enabled:
                values['inspection_cycle_enabled'] = True
            equipment.write(values)

    @api.depends('inspection_cycle_enabled', 'inspection_cycle_paused', 'inspection_cycle_next_date')
    def _compute_inspection_cycle_is_due(self):
        today = self._inspection_business_today()
        for equipment in self:
            equipment.inspection_cycle_is_due = bool(
                equipment.inspection_cycle_enabled
                and not equipment.inspection_cycle_paused
                and equipment.inspection_cycle_next_date
                and equipment.inspection_cycle_next_date <= today)

    # ------------------------------------------------------------------
    # Cycle date arithmetic.  Everything is computed from the anchor date,
    # never from the previous execution date, so plans do not drift.
    # ------------------------------------------------------------------
    def _inspection_cycle_delta(self):
        self.ensure_one()
        interval = self.inspection_cycle_interval or 0
        if interval <= 0:
            raise ValidationError(_('检查间隔必须是正整数，当前为 %(value)s。', value=interval))
        if self.inspection_cycle_unit == 'days':
            return relativedelta(days=interval)
        if self.inspection_cycle_unit == 'weeks':
            return relativedelta(weeks=interval)
        if self.inspection_cycle_unit == 'months':
            return relativedelta(months=interval)
        raise ValidationError(_('未知的周期单位：%(unit)s', unit=self.inspection_cycle_unit))

    def _inspection_cycle_occurrence(self, index):
        """Return the plan date at ``index`` counted from the anchor date."""
        self.ensure_one()
        if not self.inspection_cycle_start_date:
            raise ValidationError(_('周期检查缺少首次计划日期。'))
        return self.inspection_cycle_start_date + self._inspection_cycle_delta() * index

    def _inspection_cycle_index_on_or_before(self, day):
        """Return the largest occurrence index whose date <= ``day``.

        Returns ``None`` when the plan has not started yet.
        """
        self.ensure_one()
        start = self.inspection_cycle_start_date
        day = fields.Date.to_date(day)
        if not start or day < start:
            return None
        delta = self._inspection_cycle_delta()
        if self.inspection_cycle_unit in ('days', 'weeks'):
            index = (day - start).days // delta.days
        else:
            index = (day.year - start.year) * 12 + (day.month - start.month)
        # Safety adjustments: month arithmetic clamps to the end of month, and
        # the estimate above can be off by one in both directions.
        while index > 0 and start + delta * index > day:
            index -= 1
        while start + delta * (index + 1) <= day:
            index += 1
        return index

    def _inspection_cycle_first_occurrence_on_or_after(self, day):
        self.ensure_one()
        day = fields.Date.to_date(day)
        start = self.inspection_cycle_start_date
        if not start:
            raise ValidationError(_('周期检查缺少首次计划日期。'))
        if day <= start:
            return start
        index = self._inspection_cycle_index_on_or_before(day)
        occurrence = self._inspection_cycle_occurrence(index)
        if occurrence < day:
            occurrence = self._inspection_cycle_occurrence(index + 1)
        return occurrence

    def _inspection_cycle_first_occurrence_after(self, day):
        self.ensure_one()
        day = fields.Date.to_date(day)
        start = self.inspection_cycle_start_date
        if not start:
            raise ValidationError(_('周期检查缺少首次计划日期。'))
        if day < start:
            return start
        return self._inspection_cycle_occurrence(
            self._inspection_cycle_index_on_or_before(day) + 1)

    def _inspection_cycle_plan_dates(self, start_day, end_day):
        """List plan dates inside ``[start_day, end_day]``.

        Computed from the anchor with the same rules as the scheduler.  It never
        creates records and ignores enabled/paused: callers decide whether the
        equipment is currently planned.
        """
        self.ensure_one()
        start_day = fields.Date.to_date(start_day)
        end_day = fields.Date.to_date(end_day)
        if not self.inspection_cycle_start_date or end_day < start_day:
            return []
        first = self._inspection_cycle_first_occurrence_on_or_after(start_day)
        if first > end_day:
            return []
        dates = []
        index = self._inspection_cycle_index_on_or_before(first)
        while True:
            occurrence = self._inspection_cycle_occurrence(index)
            if occurrence > end_day:
                break
            dates.append(occurrence)
            index += 1
        return dates

    def _inspection_cycle_is_generated(self, day):
        """Whether an active inspection already covers plan date ``day``.

        Mirrors the generation de-duplication: a cycle inspection for the same
        cycle date, or a batch/cron inspection for the same business date,
        means this plan occurrence must not be shown or generated again.
        """
        self.ensure_one()
        day = fields.Date.to_date(day)
        Inspection = self.env['qc.inspection'].sudo()
        if Inspection.search_count([
            ('equipment_id', '=', self.id),
            ('inspection_cycle_date', '=', day),
            ('state', '!=', 'canceled'),
        ], limit=1):
            return True
        return bool(Inspection.search_count([
            ('equipment_id', '=', self.id),
            ('inspection_date', '=', day),
            ('inspection_source', 'in', ('batch', 'cron')),
            ('state', '!=', 'canceled'),
        ], limit=1))

    def _inspection_cycle_recompute_next_date(self, today=None):
        """Recompute the next planned date from the current configuration.

        Used when the cycle configuration changes.  Already generated plan
        dates are skipped, so changing the interval never duplicates an existing
        occurrence.  Past occurrences are not generated retroactively.
        """
        for equipment in self:
            if not equipment.inspection_cycle_enabled or not equipment.inspection_cycle_start_date:
                equipment.sudo().write({'inspection_cycle_next_date': False})
                continue
            today = fields.Date.to_date(today) if today else equipment._inspection_business_today()
            next_date = equipment._inspection_cycle_first_occurrence_on_or_after(today)
            while equipment._inspection_cycle_is_generated(next_date):
                next_date = equipment._inspection_cycle_first_occurrence_after(next_date)
            equipment.sudo().write({'inspection_cycle_next_date': next_date})

    # ------------------------------------------------------------------
    # Persistence / access
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if set(vals) & set(CYCLE_EDIT_FIELDS) and not self.env.su \
                    and not self.env.user.has_group('inspection.group_manager'):
                raise AccessError(_('只有检查主管可以配置周期检查。'))
        equipments = super().create(vals_list)
        for equipment, vals in zip(equipments, vals_list):
            if any(key in vals for key in CYCLE_SCHEDULE_FIELDS):
                equipment._inspection_cycle_recompute_next_date()
        return equipments

    def write(self, vals):
        cycle_edit = set(vals) & set(CYCLE_EDIT_FIELDS)
        if (cycle_edit and not self.env.su
                and not self.env.user.has_group('inspection.group_manager')):
            raise AccessError(_('只有检查主管可以修改周期检查配置。'))
        result = super().write(vals)
        if set(vals) & set(CYCLE_SCHEDULE_FIELDS):
            for equipment in self:
                equipment._inspection_cycle_recompute_next_date()
        return result

    @api.constrains('inspection_cycle_enabled', 'inspection_cycle_interval',
                    'inspection_cycle_unit', 'inspection_cycle_start_date',
                    'inspection_test_id', 'category_id')
    def _check_inspection_cycle_config(self):
        for equipment in self:
            if not equipment.inspection_cycle_enabled:
                continue
            if not equipment.category_id:
                raise ValidationError(_('启用周期检查前，请先选择设备类别。'))
            if not equipment.inspection_test_id:
                raise ValidationError(_('启用周期检查前，请先选择默认检查方案。'))
            if (equipment.inspection_cycle_interval or 0) <= 0:
                raise ValidationError(_('检查间隔必须是正整数，当前为 %(value)s。', value=equipment.inspection_cycle_interval))
            if equipment.inspection_cycle_unit not in CYCLE_UNITS:
                raise ValidationError(_('请选择有效的周期单位。'))
            if not equipment.inspection_cycle_start_date:
                raise ValidationError(_('启用周期检查时，请填写首次计划日期。'))
            if not equipment.inspection_supervisor_id:
                raise ValidationError(_(
                    '启用周期检查前，请先选择检查主管（异常检查需要明确的主管确认）。'))
            # Do not rely on _check_inspection_category being triggered: a
            # write that only toggles the cycle must validate the scheme too.
            equipment.inspection_test_id._check_equipment_compatibility(equipment)

    # ------------------------------------------------------------------
    # Manual entry points (all reuse qc.inspection, no second creation path)
    # ------------------------------------------------------------------
    def action_generate_inspection(self):
        self.ensure_one()
        record = self.env['qc.inspection'].generate_for_equipment(self)
        return self._inspection_form_action(record)

    def action_view_inspections(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action['domain'] = [('equipment_id', '=', self.id)]
        action['context'] = {'default_equipment_id': self.id}
        return action

    def _inspection_filtered_action(self, domain):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('inspection.action_inspections')
        action['domain'] = [('equipment_id', '=', self.id)] + domain
        action['context'] = {'default_equipment_id': self.id}
        return action

    def action_view_last_completed_inspection(self):
        self.ensure_one()
        if not self.inspection_last_completed_id:
            raise UserError(_('该设备还没有已完成的检查记录。'))
        return self._inspection_form_action(self.inspection_last_completed_id)

    def action_view_waiting_inspections(self):
        return self._inspection_filtered_action([('state', '=', 'waiting')])

    def action_view_ready_inspections(self):
        return self._inspection_filtered_action([('state', 'in', ('draft', 'ready'))])

    def action_generate_due_cycle_inspection(self):
        """Business-friendly equivalent of one scheduler run for this device.

        It calls the very same method as the cron job, so it is convenient for
        learning and for checking a device without waiting, but it does not
        pretend that the background scheduler ran.
        """
        self.ensure_one()
        if not self.env.user.has_group('inspection.group_manager'):
            raise AccessError(_('只有检查主管可以按周期生成检查。'))
        if not self.inspection_cycle_enabled:
            raise UserError(_('该设备尚未启用周期检查。'))
        if self.inspection_cycle_paused:
            raise UserError(_('该设备的周期检查已暂停，请先恢复。'))
        record = self.env['qc.inspection'].generate_cycle_for_equipment(self)
        if not record:
            raise UserError(_(
                '当前没有到期的周期检查。下一次计划日期：%(date)s',
                date=self.inspection_cycle_next_date or _('未设置')))
        return self._inspection_form_action(record)


class MaintenanceRequest(models.Model):
    _inherit = 'maintenance.request'

    inspection_id = fields.Many2one('qc.inspection', string='来源检查', copy=False,
                                    ondelete='restrict', check_company=True)
    inspection_source_label = fields.Char(
        '来源', compute='_compute_inspection_source_label',
        help='仅用于看板显示“来源：设备检查 + 检查编号”，不参与任何业务判断。')

    _inspection_unique = models.Constraint('unique(inspection_id)', '同一检查不能重复创建维修请求。')

    @api.depends('inspection_id')
    def _compute_inspection_source_label(self):
        for request in self:
            if not request.inspection_id:
                request.inspection_source_label = False
                continue
            try:
                request.inspection_source_label = _(
                    '设备检查 %(number)s', number=request.inspection_id.name)
            except AccessError:
                # Keep the existing access rules: never sudo to leak a record
                # number to a user who cannot read the source inspection.
                request.inspection_source_label = _('设备检查（无权查看）')
