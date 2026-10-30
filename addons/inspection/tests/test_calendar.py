# License AGPL-3.0-or-later.
"""检查日历测试（§5 新方案：不落库的计划预览 + qc.inspection 原生日历）。

覆盖：
- calendar 视图/动作/菜单接入 qc.inspection，且不存在持久化镜像模型；
- 只读 RPC 只在可见日期范围内现算计划，不创建任何记录；
- 暂停/关闭无计划，生成真实检查后不重复，取消后不补计划；
- 设备/方案/检查员筛选与权限、公司范围作用于计划；
- 数量/范围上限有明确提示。
"""
from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.addons.base.tests.common import BaseCommon


@tagged('post_install', '-at_install')
class TestInspectionCalendar(BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.category = cls.env['maintenance.equipment.category'].create({
            'name': '日历设备',
            'company_id': cls.env.company.id,
        })
        cls.supervisor = new_test_user(
            cls.env, login='calendar_supervisor',
            groups='inspection.group_manager', company_id=cls.env.company.id)
        cls.test = cls._create_scheme(cls.env, cls.category, '日历方案')
        cls.today = cls.env['maintenance.equipment']._inspection_business_today()
        # Demo database: the demo cycle devices would also appear in previews.
        # Pause them inside the test transaction (rolled back afterwards).
        cls.env['maintenance.equipment'].sudo().search([
            ('inspection_cycle_enabled', '=', True),
        ]).write({'inspection_cycle_paused': True})

    @classmethod
    def _create_scheme(cls, env, category, name, company=None):
        vals = {
            'name': name,
            'inspection_equipment_use': True,
            'equipment_category_ids': [Command.set(category.ids)],
            'test_lines': [
                Command.create({
                    'name': '是否渗漏',
                    'type': 'qualitative',
                    'ql_values': [
                        Command.create({'name': '无渗漏', 'ok': True}),
                        Command.create({'name': '渗漏', 'ok': False}),
                    ],
                }),
                Command.create({
                    'name': '计数',
                    'type': 'quantitative',
                    'min_value': 0,
                    'max_value': 10,
                    'uom_id': cls.unit.id,
                }),
            ],
        }
        if company:
            vals['company_id'] = company.id
        return env['qc.test'].create(vals)

    # ------------------------------------------------------------------
    # 测试工具
    # ------------------------------------------------------------------
    def _equipment(self, **values):
        vals = {
            'name': '日历设备',
            'company_id': self.env.company.id,
            'category_id': self.category.id,
            'inspection_test_id': self.test.id,
        }
        vals.update(values)
        return self.env['maintenance.equipment'].create(vals)

    def _cycle_equipment(self, **values):
        vals = {
            'inspection_supervisor_id': self.supervisor.id,
            'inspection_cycle_enabled': True,
            'inspection_cycle_interval': 30,
            'inspection_cycle_unit': 'days',
            'inspection_cycle_start_date': self.today,
        }
        vals.update(values)
        return self._equipment(**vals)

    def _preview(self, start=None, end=None, **kwargs):
        start = start or self.today
        end = end or (start + relativedelta(days=60))
        return self.env['qc.inspection'].get_cycle_plan_preview(
            start, end, **kwargs)

    # ------------------------------------------------------------------
    # 日历视图/动作/菜单接入真实模型
    # ------------------------------------------------------------------
    def test_calendar_view_action_and_menu(self):
        view = self.env.ref('inspection.view_inspection_calendar')
        self.assertEqual(view.model, 'qc.inspection')
        self.assertEqual(view.type, 'calendar')
        arch = view.arch
        for token in ('js_class="inspection_calendar"',
                      'date_start="inspection_date"',
                      'create_name_field="inspection_calendar_title"',
                      'create="0"', 'edit="0"', 'delete="0"', 'quick_create="0"',
                      'event_open_popup="1"'):
            self.assertIn(token, arch, '日历视图缺少只读/字段配置：%s' % token)

        action = self.env.ref('inspection.action_inspection_calendar')
        self.assertEqual(action.res_model, 'qc.inspection')
        self.assertIn('calendar', action.view_mode.split(','))
        self.assertTrue(action.search_view_id)

        menu = self.env.ref('inspection.menu_inspection_calendar')
        self.assertEqual(menu.parent_id, self.env.ref('inspection.menu_inspection'))
        self.assertEqual(menu.sequence, 15)

        # 新方案不落库：旧镜像模型必须不存在。
        self.assertNotIn('inspection.calendar.event', self.env.registry.models)

    def test_calendar_reuses_real_inspection_fields(self):
        equipment = self._cycle_equipment()
        inspection = self.env['qc.inspection'].generate_for_equipment(equipment)
        self.assertTrue(inspection.inspection_calendar_title)
        self.assertTrue(inspection.inspection_calendar_state_label)
        self.assertIn('草稿', inspection.inspection_calendar_state_label)
        self.assertEqual(inspection.inspection_calendar_color, 5)

    # ------------------------------------------------------------------
    # RPC：范围、只读
    # ------------------------------------------------------------------
    def test_preview_only_in_visible_range(self):
        equipment = self._cycle_equipment()
        preview = self._preview(self.today, self.today + relativedelta(days=60))
        dates = [fields.Date.to_date(item['inspection_date'])
                 for item in preview['items']]
        self.assertEqual(dates, [
            self.today,
            self.today + relativedelta(days=30),
            self.today + relativedelta(days=60),
        ])
        self.assertFalse(preview['truncated'])
        for item in preview['items']:
            self.assertLess(item['id'], 0, '计划项必须用负数 id 与真实检查区分')
            self.assertEqual(item['inspection_calendar_state_label'],
                             '计划（未生成检查单）')
            self.assertEqual(item['inspection_calendar_color'], 4)
            self.assertEqual(item['equipment_id'][0], equipment.id)

        before = self._preview(self.today - relativedelta(days=30),
                               self.today - relativedelta(days=1))
        self.assertFalse(before['items'], '首次日期之前不应出现计划')

        crossing = self._preview(self.today - relativedelta(days=30),
                                 self.today + relativedelta(days=30))
        self.assertTrue(crossing['items'])
        self.assertTrue(all(fields.Date.to_date(item['inspection_date']) >= self.today
                            for item in crossing['items']),
                        '过去日期只显示真实检查，计划必须从业务今天开始')

    def test_preview_is_read_only(self):
        equipment = self._cycle_equipment()
        counts = {
            'qc.inspection': self.env['qc.inspection'].search_count([]),
            'maintenance.request': self.env['maintenance.request'].search_count([]),
            'mail.activity': self.env['mail.activity'].search_count([]),
        }
        next_date = equipment.inspection_cycle_next_date
        for days in (0, 7, 30, 180, 400):
            self._preview(self.today, self.today + relativedelta(days=days))
        self.assertEqual(
            self.env['qc.inspection'].search_count([]), counts['qc.inspection'])
        self.assertEqual(
            self.env['maintenance.request'].search_count([]),
            counts['maintenance.request'])
        self.assertEqual(
            self.env['mail.activity'].search_count([]), counts['mail.activity'])
        self.assertEqual(equipment.inspection_cycle_next_date, next_date)
        self.assertNotIn('inspection.calendar.event', self.env.registry.models)

    # ------------------------------------------------------------------
    # 暂停/关闭、生成后去重、取消不补
    # ------------------------------------------------------------------
    def test_preview_respects_pause_and_disable(self):
        equipment = self._cycle_equipment()
        self.assertTrue(self._preview()['items'])

        equipment.write({'inspection_cycle_paused': True})
        self.assertFalse(self._preview()['items'], '暂停后不应出现计划')

        equipment.write({'inspection_cycle_paused': False})
        self.assertTrue(self._preview()['items'])

        equipment.write({'inspection_cycle_enabled': False})
        self.assertFalse(self._preview()['items'], '关闭周期后不应出现计划')

    def test_preview_dedupes_generated_and_canceled(self):
        equipment = self._cycle_equipment()
        self.assertIn(
            fields.Date.to_string(self.today),
            [item['inspection_date'] for item in self._preview(
                self.today, self.today)['items']],
        )

        record = self.env['qc.inspection'].generate_cycle_for_equipment(equipment)
        self.assertTrue(record)
        self.assertTrue(equipment._inspection_cycle_is_generated(self.today))
        self.assertFalse(
            self._preview(self.today, self.today)['items'],
            '已生成真实检查的计划日期不应重复显示计划',
        )

        # 取消：真实记录保留取消状态，日历不再自动补一个计划项。
        record.action_cancel()
        self.assertEqual(record.state, 'canceled')
        self.assertFalse(self._preview(self.today, self.today)['items'],
                         '取消的周期检查不应自动变回计划')
        later = self._preview(self.today + relativedelta(days=1),
                              self.today + relativedelta(days=45))
        self.assertTrue(later['items'], '后续期次仍应正常预览')

    # ------------------------------------------------------------------
    # 上限与提示
    # ------------------------------------------------------------------
    def test_preview_item_limit_and_range_cap(self):
        self._cycle_equipment(inspection_cycle_interval=1)
        limited = self._preview(self.today, self.today + relativedelta(days=30),
                                limit=5)
        self.assertEqual(len(limited['items']), 5)
        self.assertTrue(limited['truncated'])
        self.assertTrue(limited['message'])

        capped = self._preview(self.today, self.today + relativedelta(days=1000))
        self.assertTrue(capped['truncated'])
        self.assertTrue(capped['message'])
        max_day = fields.Date.to_string(
            self.today + relativedelta(days=400))
        self.assertTrue(all(item['inspection_date'] <= max_day
                            for item in capped['items']),
                        '超出范围上限的日期不能被悄悄显示或遗漏')

    # ------------------------------------------------------------------
    # 设备/方案/检查员筛选与权限
    # ------------------------------------------------------------------
    def test_preview_domain_filters(self):
        inspector = new_test_user(
            self.env, login='calendar_filter_inspector',
            groups='inspection.group_user', company_id=self.env.company.id)
        equipment_one = self._cycle_equipment(
            name='筛选设备一', technician_user_id=inspector.id)
        second_scheme = self._create_scheme(self.env, self.category, '第二方案')
        equipment_two = self._cycle_equipment(
            name='筛选设备二', inspection_test_id=second_scheme.id)

        by_equipment = self._preview(
            domain=[['equipment_id', '=', equipment_one.id]])
        self.assertTrue(by_equipment['items'])
        self.assertTrue(all(item['equipment_id'][0] == equipment_one.id
                            for item in by_equipment['items']))

        by_scheme = self._preview(domain=[['test', '=', second_scheme.id]])
        self.assertTrue(by_scheme['items'])
        self.assertTrue(all(item['test'][0] == second_scheme.id
                            for item in by_scheme['items']))

        by_user = self._preview(domain=[['user', '=', inspector.id]])
        self.assertTrue(by_user['items'])
        self.assertTrue(all(item['equipment_id'][0] == equipment_one.id
                            for item in by_user['items']))
        self.assertTrue(equipment_two)

        # 状态类筛选只显示真实检查，不混入计划。
        by_state = self._preview(domain=[['state', '=', 'success']])
        self.assertFalse(by_state['items'])
        self.assertTrue(by_state['message'])

    def test_preview_permissions(self):
        inspector = new_test_user(
            self.env, login='calendar_inspector',
            groups='inspection.group_user', company_id=self.env.company.id)
        other_inspector = new_test_user(
            self.env, login='calendar_other',
            groups='inspection.group_user', company_id=self.env.company.id)
        equipment = self._cycle_equipment(technician_user_id=inspector.id)
        # 设备记录规则要求检查员在设备上有本人检查；建一张本人草稿。
        self.env['qc.inspection'].generate_for_equipment(equipment)

        inspector_preview = self.env['qc.inspection'].with_user(
            inspector).get_cycle_plan_preview(
                self.today, self.today + relativedelta(days=30))
        self.assertTrue(inspector_preview['items'])
        self.assertTrue(all(item['equipment_id'][0] == equipment.id
                            for item in inspector_preview['items']))

        other_preview = self.env['qc.inspection'].with_user(
            other_inspector).get_cycle_plan_preview(
                self.today, self.today + relativedelta(days=30))
        self.assertFalse(other_preview['items'], '检查员不能看到别人设备的计划')

        plain = new_test_user(
            self.env, login='calendar_plain', groups='base.group_user',
            company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            self.env['qc.inspection'].with_user(plain).get_cycle_plan_preview(
                self.today, self.today + relativedelta(days=30))

    def test_preview_company_isolation(self):
        equipment_one = self._cycle_equipment(name='本公司日历设备')
        company_two = self._create_company(name='日历第二公司')
        supervisor_two = new_test_user(
            self.env, login='calendar_supervisor_two',
            groups='inspection.group_manager', company_id=company_two.id)
        category_two = self.env['maintenance.equipment.category'].create({
            'name': '第二公司日历设备', 'company_id': company_two.id})
        scheme_two = self._create_scheme(self.env, category_two, '第二公司方案', company=company_two)
        equipment_two = self._cycle_equipment(
            name='第二公司日历设备', company_id=company_two.id,
            category_id=category_two.id, inspection_test_id=scheme_two.id,
            inspection_supervisor_id=supervisor_two.id)
        self.assertTrue(equipment_one)
        self.assertTrue(equipment_two)

        QcInspection = self.env['qc.inspection']
        # Real users (not the superuser, which bypasses record rules) prove
        # that the company rule filters both real records and plan previews.
        preview_one = QcInspection.with_user(self.supervisor).get_cycle_plan_preview(
            self.today, self.today + relativedelta(days=60))
        preview_two = QcInspection.with_user(supervisor_two).get_cycle_plan_preview(
            self.today, self.today + relativedelta(days=60))
        self.assertTrue(preview_one['items'])
        self.assertTrue(preview_two['items'])
        self.assertTrue(all(item['equipment_id'][0] == equipment_one.id
                            for item in preview_one['items']))
        self.assertTrue(all(item['equipment_id'][0] == equipment_two.id
                            for item in preview_two['items']))

    def test_equipment_preview_entry_point(self):
        equipment = self._cycle_equipment()
        preview = equipment.get_inspection_plan_preview(
            self.today, self.today + relativedelta(days=60))
        self.assertTrue(preview['items'])
        self.assertTrue(all(item['equipment_id'][0] == equipment.id
                            for item in preview['items']))
