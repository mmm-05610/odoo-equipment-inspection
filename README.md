# Equipment Inspection

基于 **Odoo 19 Maintenance + OCA Quality Control** 的设备检查练习项目。复用已有设备、维修、检查方案和检查单模型，自写部分连接检查执行、周期计划、异常维修与活动提醒。

- **在线演示**：[青禾制造设备检查](https://odoo.maomaokingdom.top/demo)
- **角色**：检查主管 `inspection.manager`；检查员 `inspection.inspector`
- **公开演示密码**：`InspectionDemo2026!`
- **重新测试**：演示入口提供“重置演示数据”，所有访客共享同一个库；重置会清除所有人的本轮操作并使登录失效。

这是学习与展示项目，不是生产系统。请勿上传真实业务或个人资料。

## 可以体验什么

| 入口 | 功能 |
| --- | --- |
| 设备 → 检查 | 最近完成结果、待处理数量、最近 5 条、检查方案、主管和单开关周期配置 |
| 检查 → 检查方案 | 按设备类别限制适用范围，同类设备有日常、验收、专项方案 |
| 检查 → 检查记录 | 选项与数值检查、当次标准快照、结果冻结、异常主管确认 |
| 异常检查 → 转维修 | 复用 Maintenance 请求，保留异常项目与标准，重复点击不重复创建 |
| 检查 → 检查日历 | 历史记录与按可见日期范围计算的未来计划；计划预览不落库 |
| 检查 → 批量生成 | 临时检查与到期检查，共用生成与去重规则 |
| 设备活动 | 按检查状态同步负责人和活动，不能绕过检查直接完成活动 |

工厂数据：3 个团队、12 台设备、9 套方案、27 个项目、24 张检查、8 张维修。每次重置以当天生成相对日期。后台 cron 在公开演示中关闭，可用“立即生成到期检查”体验同一生成逻辑。

## 代码与复用边界

```text
addons/inspection/       自写 addon：模型扩展、视图、日历、权限、测试、demo
scripts/                 获取固定版本依赖、干净库 demo 加载
deploy/                 容器、部署、访客入口与固定范围重置服务
docs/                   操作、架构、部署与验收说明
```

`maintenance`、`mail` 来自 Odoo 19 镜像；`quality_control_oca` 固定在 OCA/manufacture 提交 `635f19675e4f606bd719c893175a65305ad562c6`。构建前由脚本提取该 addon，运行时通过 manifest 的依赖关系加载。没有复制整个 ERP 或重新实现质量检查框架。

- [操作指南](docs/DEMO.md)
- [架构与边界](docs/ARCHITECTURE.md)
- [部署与重置](docs/DEPLOYMENT.md)
- [验收范围](docs/ACCEPTANCE.md)
- [Addon 说明](addons/inspection/README.md)

## 构建

```bash
./scripts/fetch-deps.sh
docker build -f deploy/Postgres.Dockerfile -t inspection-postgres:15 .
docker build -f deploy/Dockerfile -t inspection-public:19.0.1.3 .
```

部署脚本使用独立 Compose 项目 `inspection_public`，不会操作其他项目的数据卷。运行前请阅读部署说明，尤其是公开重置的共享数据边界。数据库密码与 Odoo master password 由服务器生成，不进入仓库。

## 验证与限制

已有周期、月末/闰年、权限、快照、活动、日历、并发去重和业务集成测试。网页按主管与检查员实际执行，具体结果见验收说明。自动测试不等于生产安全或压力测试。

自写界面仍以中文为主；未来计划预览限制 400 天 / 2000 条；真实日历记录需经事件详情进入表单。没有完成生产压力测试或长时间跨月调度验证。

## 许可证

[AGPL-3.0](LICENSE)。Odoo 与 OCA 依赖各自保留上游许可证及作者信息。源码公开供审阅、学习和按许可证使用。
