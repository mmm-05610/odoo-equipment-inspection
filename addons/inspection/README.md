# Equipment Inspection

Odoo 19 设备检查学习 addon，版本 `19.0.1.3`，AGPL-3。复用官方 `maintenance`、OCA `quality_control_oca` 和 `mail`，连接设备、检查、异常维修、周期、日历与活动。

## 演示与运行

在线演示、两套账号和公开重置入口见[项目首页](../../README.md)。操作流程见[演示指南](../../docs/DEMO.md)，部署见[部署说明](../../docs/DEPLOYMENT.md)。

青禾制造初始数据包含 3 个团队、12 台设备、9 套检查方案、24 张检查和 8 张维修。后台 cron 默认关闭，可通过设备“立即生成到期检查”体验同一生成逻辑。

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

干净演示库先用 `--without-demo=all` 安装依赖与本 addon，再由 Odoo shell 执行项目 `scripts/load_factory_demo.py`。这样不会带入上游模块的随机 demo。加载器拒绝覆盖有业务数据的库，同库重复执行不重置用户修改。日常重启无需加载脚本。

## 验证

2026-10-31：84 项 post-install 测试、7 项网页业务验收通过，最终 8071 两账号登录与初始数量复核通过。验收范围见仓库 `docs/ACCEPTANCE.md`，原始运行日志保留在维护者本地，不随源码公开。验收操作在副本进行，当前库保留初始演示状态。

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
