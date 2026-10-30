# License AGPL-3.0-or-later.
"""设备检查日历：原生日历视图 + 只读的未来周期计划预览。

设计要点（与《设备检查完整实施方案》§5 一致）：

* 日历建在 ``qc.inspection`` 上，真实检查就是 OCA 的检查单，不复制、不落库。
* 未来计划不持久化：日历前端在每次加载当前可见日期范围时，调用本文件的
  只读 RPC ``get_cycle_plan_preview`` 现算计划日期，合并到前端渲染。
* 日期算法全部复用 ``maintenance.equipment`` 的周期 helper，与周期任务同源：
  ``_inspection_cycle_plan_dates`` / ``_inspection_cycle_is_generated`` 等。
* 同一期已有真实检查（含已取消）就不再显示计划；取消不自动补计划。
* 浏览日历只读：RPC 不创建检查、活动或任何数据库记录。
* 计划计算不 sudo 读取业务对象：设备、方案、检查员都按当前用户的记录规则
  读取；只有“该期是否已有记录”的去重判断会读取日期对，且不返回给前端。
"""
from datetime import timedelta

from odoo import api, fields, models, _

# 计划项颜色（web 端 o_calendar_color_N）：蓝色，和真实检查的状态色区分。
PLAN_COLOR = 4
STATE_COLORS = {
    'draft': 5,
    'ready': 2,
    'waiting': 3,
    'success': 10,
    'failed': 1,
    'canceled': 0,
}

# 单次预览的容量上限；超过时明确提示，不静默截断。
MAX_PREVIEW_ITEMS = 2000
# 单次查询的日期跨度上限（年视图约 365 天，留出月视图跨月的余量）。
MAX_PREVIEW_DAYS = 400
# 参与计算的最大设备数（防御性上限，正常情况下远小于它）。
MAX_PREVIEW_EQUIPMENTS = 2000

# 搜索视图里只有这些字段的筛选能映射到“计划”（计划没有检查单状态）。
PLAN_FILTER_FIELDS = ('equipment_id', 'test', 'user')
STATUS_FILTER_FIELDS = ('state', 'success', 'inspection_conclusion',
                        'inspection_source', 'inspection_cycle_date')


class QcInspection(models.Model):
    _inherit = 'qc.inspection'

    # 日历标题/状态/颜色都是展示用非存储字段：真实检查实时计算，
    # 未来计划由 RPC 直接放进 rawRecord，不写数据库。
    inspection_calendar_title = fields.Char(
        '日历标题', compute='_compute_inspection_calendar_title')
    inspection_calendar_state_label = fields.Char(
        '日历状态', compute='_compute_inspection_calendar_state_label')
    inspection_calendar_cycle_label = fields.Char(
        '周期来源', compute='_compute_inspection_calendar_cycle_label')
    inspection_calendar_color = fields.Integer(
        '日历颜色', compute='_compute_inspection_calendar_color')

    @api.depends('equipment_id.name', 'state', 'name')
    def _compute_inspection_calendar_title(self):
        for inspection in self:
            equipment_name = inspection.equipment_id.sudo().name or _('设备')
            inspection.inspection_calendar_title = _(
                '检查｜%(equipment)s · %(state)s',
                equipment=equipment_name,
                state=self._calendar_state_label(inspection.state),
            )

    @api.depends('state')
    def _compute_inspection_calendar_state_label(self):
        for inspection in self:
            inspection.inspection_calendar_state_label = \
                self._calendar_state_label(inspection.state)

    @api.depends('inspection_cycle_date')
    def _compute_inspection_calendar_cycle_label(self):
        # 真实检查若是周期任务生成的，可显示当次计划日期；普通检查为空。
        for inspection in self:
            inspection.inspection_calendar_cycle_label = (
                _('周期任务，计划日期 %(date)s',
                  date=inspection.inspection_cycle_date)
                if inspection.inspection_cycle_date else False)

    @api.depends('state')
    def _compute_inspection_calendar_color(self):
        for inspection in self:
            inspection.inspection_calendar_color = STATE_COLORS.get(
                inspection.state, 0)

    @api.model
    def _calendar_state_label(self, state):
        labels = dict(
            self._fields['state']._description_selection(self.env))
        return labels.get(state, state or '')

    # ------------------------------------------------------------------
    # 只读 RPC：当前可见日期范围内的未来周期计划（不落库）
    # ------------------------------------------------------------------
    @api.model
    def get_cycle_plan_preview(self, date_start, date_end, domain=None, limit=None):
        """返回 [date_start, date_end] 内尚未生成的周期计划（仅供日历渲染）。

        :param date_start: 可见范围开始日期（含）
        :param date_end: 可见范围结束日期（含）
        :param domain: 当前搜索视图 domain；只把设备/方案/检查员条件映射到计划
        :param limit: 计划项数量上限，默认/最大 2000
        :return: dict(items, truncated, limit, message)
        """
        self.check_access('read')
        start = fields.Date.to_date(date_start) if date_start else False
        end = fields.Date.to_date(date_end) if date_end else False
        if not start or not end or end < start:
            return {'items': [], 'truncated': True, 'limit': 0,
                    'message': _('日期范围无效，无法计算未来检查计划。')}
        limit = self._plan_preview_limit(limit)
        truncated = False
        messages = []

        # “未来计划”只从业务今天开始：过去日期只显示真实检查（含已取消），
        # 不能让漏生成的历史周期看起来像还会发生的计划。
        business_today = self.env['maintenance.equipment']._inspection_business_today()
        if end < business_today:
            return {'items': [], 'truncated': False, 'limit': limit,
                    'message': False}
        start = max(start, business_today)

        max_end = start + timedelta(days=MAX_PREVIEW_DAYS)
        if end > max_end:
            end = max_end
            truncated = True
            messages.append(_(
                '查询范围超过 %(days)s 天，已只计算前 %(days)s 天。',
                days=MAX_PREVIEW_DAYS))

        domain = domain if isinstance(domain, (list, tuple)) else []
        if self._plan_preview_has_status_filter(domain):
            # 状态类筛选只适用于真实检查；此时不混入计划项。
            return {'items': [], 'truncated': False, 'limit': limit,
                    'message': _('当前筛选条件只显示真实检查，未来计划未显示。')}

        equip_domain = [
            ('inspection_cycle_enabled', '=', True),
            ('inspection_cycle_paused', '=', False),
            ('inspection_cycle_start_date', '!=', False),
            ('inspection_test_id', '!=', False),
        ]
        if not self.env.user.has_group('inspection.group_manager'):
            # 检查员只看自己负责（技术员或检查主管）的设备；设备记录规则依旧生效。
            equip_domain = ['|',
                            ('technician_user_id', '=', self.env.user.id),
                            ('inspection_supervisor_id', '=', self.env.user.id),
                            ] + equip_domain
        equip_domain += self._plan_preview_filter_domain(domain)

        Equipment = self.env['maintenance.equipment']
        equipments = Equipment.search(
            equip_domain, limit=MAX_PREVIEW_EQUIPMENTS + 1, order='id')
        if len(equipments) > MAX_PREVIEW_EQUIPMENTS:
            equipments = equipments[:MAX_PREVIEW_EQUIPMENTS]
            truncated = True
            messages.append(_(
                '设备数量超过 %(count)s 台，已只计算前 %(count)s 台。',
                count=MAX_PREVIEW_EQUIPMENTS))

        covered_dates = self._plan_preview_covered_dates(equipments, start, end)
        items = []
        seq = 0
        for equipment in equipments:
            for day in equipment._inspection_cycle_plan_dates(start, end):
                if ((equipment.id, day) in covered_dates
                        or equipment._inspection_cycle_is_generated(day)):
                    continue
                if len(items) >= limit:
                    truncated = True
                    messages.append(_(
                        '未来计划超过 %(limit)s 条，已截断；请缩小日期范围或增加筛选。',
                        limit=limit))
                    break
                seq += 1
                items.append(self._plan_preview_item(equipment, day, seq))
            if len(items) >= limit:
                break

        return {
            'items': items,
            'truncated': truncated,
            'limit': limit,
            'message': ' '.join(messages) or False,
        }

    @api.model
    def _plan_preview_limit(self, limit):
        try:
            limit = int(limit or MAX_PREVIEW_ITEMS)
        except (TypeError, ValueError):
            limit = MAX_PREVIEW_ITEMS
        return max(1, min(limit, MAX_PREVIEW_ITEMS))

    @api.model
    def _plan_preview_has_status_filter(self, domain):
        for leaf in domain:
            if (isinstance(leaf, (list, tuple)) and len(leaf) == 3
                    and leaf[0] in STATUS_FILTER_FIELDS):
                return True
        return False

    @api.model
    def _plan_preview_filter_domain(self, domain):
        """把 qc.inspection 搜索域里的设备/方案/检查员条件映射成设备域。"""
        mapped = []
        for leaf in domain:
            if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
                continue
            field_name, operator, value = leaf
            if field_name not in PLAN_FILTER_FIELDS:
                continue
            if field_name == 'equipment_id':
                if operator in ('=', '!=', 'in', 'not in', 'child_of',
                                'parent_of'):
                    mapped.append(('id', operator, value))
                elif operator in ('ilike', 'not ilike', 'like', 'not like'):
                    mapped.append(('name', operator, value))
            elif field_name == 'test':
                mapped.append(('inspection_test_id', operator, value))
            elif field_name == 'user':
                mapped.append(('technician_user_id', operator, value))
        return mapped

    @api.model
    def _plan_preview_covered_dates(self, equipments, start, end):
        """该范围内已有周期检查的 (设备 id, 日期) 集合（含已取消）。

        Lead 的 ``_inspection_cycle_is_generated`` 只把未取消的记录算作已生成；
        业务规则是“取消不自动补计划”，所以这里额外做取消记录的日期去重。
        只读取日期对，不向前端返回检查单的名称/状态/数量，避免越权泄露。
        """
        if not equipments:
            return set()
        inspections = self.sudo().search([
            ('equipment_id', 'in', equipments.ids),
            ('inspection_cycle_date', '>=', start),
            ('inspection_cycle_date', '<=', end),
        ])
        return {(inspection.equipment_id.id, inspection.inspection_cycle_date)
                for inspection in inspections}

    @api.model
    def _plan_preview_item(self, equipment, day, sequence):
        """一条未来计划的前端 rawRecord（与 calendar arch 字段对齐）。"""
        equipment_name = equipment.name
        scheme = equipment.inspection_test_id
        technician = equipment.technician_user_id
        title = _('计划｜%(equipment)s · %(scheme)s',
                  equipment=equipment_name, scheme=scheme.name)
        return {
            # 负数 id：前端据此识别“计划”，点击时只弹只读详情，不会打开检查单。
            'id': -sequence,
            'inspection_date': fields.Date.to_string(day),
            'display_name': title,
            'inspection_calendar_title': title,
            'inspection_calendar_state_label': _('计划（未生成检查单）'),
            'inspection_calendar_cycle_label':
                self._plan_preview_cycle_label(equipment),
            'inspection_calendar_color': PLAN_COLOR,
            'equipment_id': [equipment.id, equipment_name],
            'test': [scheme.id, scheme.display_name],
            'user': [technician.id, technician.display_name] if technician else False,
        }

    @api.model
    def _plan_preview_cycle_label(self, equipment):
        unit_labels = dict(
            self.env['maintenance.equipment']._fields[
                'inspection_cycle_unit']._description_selection(self.env))
        text = _('每 %(interval)s %(unit)s',
                 interval=equipment.inspection_cycle_interval,
                 unit=unit_labels.get(equipment.inspection_cycle_unit,
                                      equipment.inspection_cycle_unit))
        if equipment.inspection_cycle_start_date:
            text = '%s，%s' % (
                text, _('首次 %(date)s',
                        date=equipment.inspection_cycle_start_date))
        return text


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    def get_inspection_plan_preview(self, date_start, date_end, domain=None,
                                    limit=None):
        """设备侧的只读预览入口，委托给 qc.inspection 的同一套计算。"""
        return self.env['qc.inspection'].get_cycle_plan_preview(
            date_start, date_end, domain=domain, limit=limit)
