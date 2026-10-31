# License AGPL-3.0-or-later.
"""A coherent factory history, only loaded with this addon's demo fixtures.

All states and inspection-to-repair links are produced by business actions.
Historical completion timestamps are supplied explicitly for the sample history.
XML IDs make seeding repeatable; rerunning it never resets visitors' work.
"""
from datetime import datetime, time, timedelta
from dateutil.relativedelta import relativedelta


def _ref(env, name):
    return env.ref('inspection.' + name)


def _remember(env, name, record):
    env['ir.model.data'].create({
        'module': 'inspection', 'name': name, 'model': record._name,
        'res_id': record.id, 'noupdate': True,
    })
    return record


def _answer(record, failure, ordinal):
    for line in record.inspection_lines:
        bad = line.test_line == failure
        if line.question_type == 'qualitative':
            line.qualitative_value = line.possible_ql_values.filtered(
                lambda answer: bool(answer.ok) != bad)[:1]
        else:
            lo, hi = line.min_value, line.max_value
            # Distinct plausible observations, bounded by each frozen standard.
            value = round(lo + (hi - lo) * (0.35 + ordinal % 4 * 0.1), 2)
            if bad:
                value = round(hi + max((hi - lo) * 0.3, 0.3), 2)
            line.write({'quantitative_value': value, 'inspection_numeric_answered': True})


def seed_factory_history(env):
    if not env.ref('inspection.factory_equipment_p01', raise_if_not_found=False):
        return
    config = env['ir.config_parameter'].sudo()
    if config.get_param('inspection.factory_demo_seeded'):
        return
    today = env['maintenance.equipment']._inspection_business_today()
    Inspection = env['qc.inspection']
    # device, day offset, final state, failed question key, observation
    rows = [
        ('p01', -12, 'success', None, '早班巡视：密封干燥，运行声音平稳。'),
        ('p01', -10, 'success', None, '复核出口读数，现场未见渗漏。'),
        ('p01', -8, 'success', None, '完成例行巡视，通道畅通。'),
        ('p01', -6, 'success', None, '运行状态与前次一致，无新增异常。'),
        ('p01', -4, 'success', None, '出口压力在当次标准范围内。'),
        ('p01', -2, 'success', None, '日常检查完成，密封及声音无异常。'),
        ('p01', 0, 'ready', None, '今日早班例行检查，等待现场填写。'),
        ('p02', -9, 'success', None, '专项复查，读数稳定，阀门状态符合运行要求。'),
        ('p02', -5, 'failed', 'pump_diagnostic_pressure', '出口压力高于当次上限，保留记录待安排后续处理。'),
        ('p02', 0, 'waiting', 'pump_diagnostic_pressure', '现场压力再次偏高，阀门状态正常，请主管确认。'),
        ('p03', -10, 'failed', 'pump_acceptance_connection', '试运行时连接部位出现松动，停止验收并转维修。'),
        ('p03', 0, 'draft', None, '连接部位维修已完成，等待重新验收。'),
        ('p04', -3, 'success', None, '冷却水站例行检查，各项符合当次要求。'),
        ('p04', -1, 'success', None, '晚班巡检完成，无渗漏和异响。'),
        ('p04', 0, 'ready', None, '今日冷却水站检查，等待填写。'),
        ('p05', -2, 'canceled', None, '设备停用待保养，本次检查取消，周期已暂停。'),
        ('f01', -7, 'success', None, '东侧排风机运行稳定，防护罩完好。'),
        ('f01', -1, 'success', None, '本次巡视未复现此前异响，仍保留待确认记录。'),
        ('f01', -2, 'waiting', 'fan_daily_sound', '发现间歇异响，后续检查合格不能代替本次异常确认。'),
        ('f02', 0, 'ready', None, '双周检查任务已下发，检查前先查看关联维修进展。'),
        ('f03', -4, 'success', None, '冷却塔风机防护与振动读数符合当次要求。'),
        ('m01', -3, 'failed', 'motor_daily_temperature', '电机表面温度高于当次上限，已交电气组处理。'),
        ('i01', -6, 'failed', 'instrument_deviation', '压力表示值与参考值偏差过大，需仪表组复核。'),
        ('i02', -1, 'waiting', 'instrument_display', '传感器显示间歇跳动，等待主管确认。'),
    ]
    records = {}
    pressure = _ref(env, 'factory_question_pump_daily_pressure')
    for ordinal, (device, offset, outcome, failure_key, note) in enumerate(rows, 1):
        xmlid = 'factory_history_%02d' % ordinal
        record = env.ref('inspection.' + xmlid, raise_if_not_found=False)
        if record:
            records[ordinal] = record
            continue
        equipment = _ref(env, 'factory_equipment_' + device)
        # First history uses the earlier limit. The record freezes it before
        # the current standard is restored, demonstrating real snapshot behavior.
        if ordinal == 1:
            pressure.max_value = 9
        day = today + timedelta(days=offset)
        record = Inspection.generate_for_equipment(equipment, day, source='manual')
        if ordinal == 1:
            pressure.max_value = 8
        record.internal_notes = note
        record.date = datetime.combine(day, time(1, ordinal % 60))  # UTC, morning in Shanghai
        if outcome == 'canceled':
            record.action_cancel()
        elif outcome != 'draft':
            record.action_todo()
            if outcome != 'ready':
                failure = _ref(env, 'factory_question_' + failure_key) if failure_key else None
                _answer(record, failure, ordinal)
                record.action_confirm()
                if outcome == 'failed':
                    record.action_approve()
                if outcome in ('success', 'failed'):
                    record.date_done = datetime.combine(day, time(2, ordinal % 60))
        _remember(env, xmlid, record)
        assert record.state == outcome, (xmlid, record.state, outcome)
        records[ordinal] = record

    # Three actual inspection -> repair links, including one real reassignment.
    for index, stage, schedule_offset in [(11, 'stage_3', -8), (22, 'stage_1', 0), (23, 'stage_0', 1)]:
        record = records[index]
        record.action_create_repair()
        request = record.maintenance_request_id
        request.write({
            'request_date': record.inspection_date,
            'schedule_date': datetime.combine(today + timedelta(days=schedule_offset), time(2)),
            'stage_id': env.ref('maintenance.' + stage).id,
        })
        if stage == 'stage_3':
            request.close_date = today - timedelta(days=7)
        if index == 23:
            request.write({'maintenance_team_id': _ref(env, 'factory_team_mechanical').id,
                           'user_id': _ref(env, 'factory_user_mechanic').id})
            request.message_post(body='机械组核对设备运行情况后，转交仪表组复核压力表示值。')
            request.write({'maintenance_team_id': _ref(env, 'factory_team_instrument').id,
                           'user_id': _ref(env, 'factory_user_instrument').id})
        _remember(env, 'factory_repair_from_%02d' % index, request)

    native_requests = [
        ('f02', '二号排风机｜轴承异响处理', 'stage_1', 0, 'corrective', '巡检人员报修，机械组正在检查轴承和连接部位。'),
        ('m02', '冷却塔电机｜接线盒检查与保养', 'stage_0', 2, 'preventive', '计划性保养，检查接线盒外观和密封情况。'),
        ('p05', '排水泵｜停用期间清洁保养', 'stage_0', 3, 'preventive', '设备暂时停用，清理现场后再恢复周期检查。'),
        ('f03', '冷却塔风机｜皮带定期更换', 'stage_3', -5, 'preventive', '定期保养已完成，后续检查记录可在设备页查看。'),
        ('p04', '冷却水泵｜底座紧固复查', 'stage_0', None, 'corrective', '现场人员报修，等待机械组安排时间。'),
    ]
    for index, (device, title, stage, offset, kind, notes) in enumerate(native_requests, 1):
        equipment = _ref(env, 'factory_equipment_' + device)
        values = {
            'name': title, 'equipment_id': equipment.id, 'company_id': equipment.company_id.id,
            'maintenance_team_id': equipment.maintenance_team_id.id,
            'user_id': equipment.technician_user_id.id,
            'owner_user_id': _ref(env, 'factory_user_manager').id,
            'request_date': today - timedelta(days=6 if stage == 'stage_3' else 1),
            'maintenance_type': kind, 'stage_id': env.ref('maintenance.' + stage).id,
            'description': notes,
        }
        if offset is not None:
            values['schedule_date'] = datetime.combine(today + timedelta(days=offset), time(3))
        if stage == 'stage_3':
            values['close_date'] = today - timedelta(days=4)
        request = env['maintenance.request'].create(values)
        _remember(env, 'factory_repair_native_%02d' % index, request)

    # One schedule per device; no preset future inspection records.
    schedules = [
        ('p04', 1, 'days', today + timedelta(days=1), False),
        ('p05', 1, 'weeks', today - timedelta(days=14), True),
        ('f02', 2, 'weeks', today + timedelta(days=7), False),
        ('f03', 1, 'months', today + relativedelta(day=31), False),
    ]
    for device, interval, unit, anchor, paused in schedules:
        _ref(env, 'factory_equipment_' + device).write({
            'inspection_cycle_enabled': True, 'inspection_cycle_interval': interval,
            'inspection_cycle_unit': unit, 'inspection_cycle_start_date': anchor,
            'inspection_cycle_paused': paused,
        })
    config.set_param('inspection.factory_demo_seeded', today.isoformat())
