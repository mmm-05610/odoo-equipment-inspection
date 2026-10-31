# Equipment Inspection

Odoo 19 设备检查学习 addon，版本 `19.0.1.3`，AGPL-3。复用官方 `maintenance`、OCA `quality_control_oca` 和 `mail`，连接设备、检查、异常维修、周期、日历与活动。

## 演示

[在线演示](https://odoo.maomaokingdom.top/demo)

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 检查主管 | `inspection.manager` | `InspectionDemo2026!` |
| 检查员 | `inspection.inspector` | `InspectionDemo2026!` |

登录后进入「维护保养 → 检查」。两种角色可以分别使用普通窗口和无痕窗口。

1. 主管打开「检查方案」，对比水泵的日常巡检、检修后验收、压力异常排查方案。
2. 检查员打开 P01 当天的待检查记录，选择「无渗漏」「无异响」，压力填 `6.2`，提交后应为合格。
3. 主管打开 P02 待确认记录，确认异常并转维修；在维修的「问题描述」查看异常明细，通过「来源检查」返回。
4. 打开设备 F01 的「检查」页签：最近完成合格与待确认异常同时显示，历史结论不会覆盖未处理任务。
5. 打开「检查日历」并翻到下月，查看只读未来计划。需要体验到期生成时，将 P04 的首次计划日期设为当天，保存后点击「立即生成到期检查」。

在线初始数据为 3 个团队、12 台设备、9 套方案、24 张检查、8 张维修。后台 cron 关闭，周期生成可手动触发。首页提供访客重置，清除所有访客的本轮操作并使旧登录失效，冷却 5 分钟。请勿录入真实业务或个人资料。

## 安装

本仓库本身就是一个 addon，技术名称为 `inspection`。不包含 Odoo 服务、第三方 addon 或部署环境。

需要 Odoo 19，以及 OCA [manufacture](https://github.com/OCA/manufacture) 中的 `quality_control_oca`。本项目验证过的 OCA 提交为 `635f19675e4f606bd719c893175a65305ad562c6`；`maintenance`、`mail` 及其依赖由 Odoo 提供。

将仓库克隆到自定义 addons 路径下，**目录名必须为 `inspection`**：

```bash
git clone https://github.com/mmm-05610/odoo-equipment-inspection.git custom-addons/inspection
```

把 `custom-addons` 和 OCA manufacture 目录加入 Odoo 的 `addons_path`，保留官方 addons 路径。更新应用列表，取消默认「应用」筛选，搜索并安装「Equipment Inspection」。这是扩展模块，入口位于「维护保养」，不会单独出现一个顶层应用。

自动周期检查需要启用计划操作「设备检查：按周期生成」，并让 Odoo 的 cron 线程运行；仅勾选设备周期不会启动后台调度。

## 模型扩展点

本 addon 用 `_inherit` 扩展现有模型，只新增一个批量向导；不新建“检查单据”模型，不复制 OCA 源码。

| 文件 | 运行时模型 | 主要职责 |
| --- | --- | --- |
| `models/maintenance.py` | `maintenance.equipment`、`maintenance.request` | 设备默认方案、检查主管、周期配置与锚点日期算法、页签摘要；维修请求的“来源检查”与看板来源标签 |
| `models/qc_test.py` | `qc.test` | “用于设备检查”标记、适用设备类别、使用说明、必填项目校验 |
| `models/qc_inspection.py` | `qc.inspection` | 设备生成入口（手动/批量/周期共用）、状态与权限校验、当次主管快照、周期去重、取消补建、异常转维修 |
| `models/qc_inspection_line.py` | `qc.inspection.line` | 当次标准/答案/单位快照、零值回答、结果冻结、跨单位判定 |
| `models/inspection_calendar.py` | `qc.inspection`、`maintenance.equipment` | 原生日历的展示字段与只读 RPC `get_cycle_plan_preview`；未来计划现算，不落库 |
| `models/inspection_activity.py` | `mail.activity`、`qc.inspection` | 每个检查单至多一个原生活动；类型/负责人随状态同步，完成拦截，幂等回填 |
| `wizard/generate.py` | `inspection.generate` | 批量“临时检查/到期检查”入口，调用与 cron 相同的生成方法 |

前端只做原生日历的局部扩展（`static/src/js/inspection_calendar_*.js`、`static/src/xml/inspection_calendar_templates.xml`）：切换月/周/日时按可见范围调用 RPC，合并“计划”项；不新建持久化日历模型或镜像表。

## 周期规则摘要

- 一台设备一个默认方案、一条周期规则：启用、间隔（正整数）、单位（天/周/月）、首次计划日期（锚点）、只读的下次计划日期、暂停。
- 所有计划日期从锚点用 `dateutil.relativedelta` 推算，不从上次执行日累加；每月 31 日会得到 2 月末、3 月 31 日，不漂移。
- 一个共享 `ir.cron`（“设备检查：按周期生成”）扫描所有到期设备，不给每台设备建 cron；`data/cron.xml` 默认 `active=False`。
- 生成时对设备行加锁，并用 `(equipment_id, inspection_cycle_date)` 条件唯一索引兜底；重复/并发触发返回同一张检查单。
- 生成失败不推进下次日期，错误写入设备 `inspection_cycle_last_error`，下次可重试。
- 停机错过多期只补最近一期，下一次日期推进到未来；暂停不生成，恢复同样只补最近一期。
- 取消周期检查不回退计划、调度不自动补回；检查主管可在已取消记录上“补建本期检查”。
- 周期配置只允许“检查主管”修改；配置变更会重算后续计划，但不改历史检查的标准和结果。
- 业务日期使用 `ir.config_parameter` 的 `inspection.business_timezone`（默认 `Asia/Shanghai`），不随访问者时区变化。

## 演示数据与初始化

`demo/factory_demo.xml` 定义工厂基础资料；`demo_seed.py` 通过实际业务动作生成历史、维修和活动。`demo/inspection_lang_demo.xml` 启用中文。只保留这一套业务 demo。

普通安装不加载 demo 时不会创建演示记录。启用 demo 的首次安装由 Odoo 读取 manifest 的 `demo` 文件，再由 `post_init_hook` 补齐历史；可能同时存在上游 addon 的演示数据。公开演示使用独立的干净数据初始化流程，其部署与重置服务不属于本 addon。

## 验证

2026-10-31：单开关调整后 86 项 post-install 测试通过；维修跳转修正后 24 项集成测试通过。网页已实际验证检查提交、异常转维修、返回专用表单、日历、周期、活动与权限。在线演示的访客重置和两套账号重登已实测。上述测试属于不同轮次，不相加为一个完整套件数量。

测试源码在 `tests/`，运行时需要真实 Odoo 19、PostgreSQL 和上述依赖，可在独立测试数据库中通过 Odoo 的 `--test-enable --test-tags /inspection` 执行。工厂 demo 的初始分布测试需要相应的干净演示数据，不能对已被操作过的演示库直接套用。

## 复用决定

| 需求 | 采用 | 不采用 |
| --- | --- | --- |
| 日期计算 | Python `dateutil.relativedelta`，统一从锚点计算 | 自写日期增量、OCA `base_recurrence` 旧实现 |
| 调度 | 原生 `ir.cron` 单共享任务 | APScheduler 等外部调度 |
| 提醒 | 原生 `mail.activity`，最小 `inspection_id` 关联 | 自建通知/待办模型 |
| 日历 | 原生 Calendar 视图 + JS 局部扩展 + 只读 RPC | 持久化 `inspection.calendar.event` 镜像、自写 OWL 日历 |
| 周期计划 | 设备上的周期字段 | OCA `maintenance_plan`（其生成面向维修请求，业务对象与提前生成策略不同） |
| 检查业务 | OCA `qc.test`/`qc.inspection`/`qc.inspection.line` | 自建检查模板/检查单/结果模型 |

## 范围与限制

- 原生日历真实记录需经详情进入；未来计划只读，范围上限 400 天 / 2000 条。
- 自写字段仍有中文硬编码，英文界面不是完整英文。
- 本轮没有启动演示库后台调度，未做长期跨月实际运行、多进程压力或生产验收。
- 历史版本升级脚本保留，但当前工厂 demo 采用干净库初始化，不把新 demo 灌入旧学习记录。
