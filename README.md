# Equipment Inspection

为 Odoo 19 的维护保养模块增加设备检查功能，将检查方案、现场记录和异常维修关联起来。

本模块复用官方 `maintenance` 的设备与维修请求、OCA `quality_control_oca` 的检查方案与检查记录，以及 `mail` 的活动与消息机制。

- 按设备类别选择检查方案，支持选项判断和数值范围检查。
- 保存当次检查标准与结果，方案修改不影响历史记录。
- 检查员填写结果，主管确认异常并转为维修请求。
- 按天、周、月安排周期检查，支持批量生成和日历查看。
- 在设备页查看最近结果、待办和检查历史。

**兼容版本：** Odoo 19 · **模块版本：** 1.0 · **许可证：** AGPL-3

[在线演示](https://odoo.maomaokingdom.top/demo) · [问题反馈](https://github.com/mmm-05610/odoo-equipment-inspection/issues)

## 安装

### 依赖

| 模块 | 来源 | 用途 |
| --- | --- | --- |
| `maintenance` | Odoo 官方 | 设备、保养团队、维修请求 |
| `quality_control_oca` | [OCA/manufacture，19.0 分支](https://github.com/OCA/manufacture/tree/19.0/quality_control_oca) | 检查方案、检查项目、检查记录 |
| `mail` | Odoo 官方 | 消息、活动与负责人提醒 |

OCA 依赖已验证的提交为 [`635f19675e4f`](https://github.com/OCA/manufacture/commit/635f19675e4f606bd719c893175a65305ad562c6)。其他间接依赖由 Odoo 自动安装。

1. 将本仓库克隆到自定义 addons 路径下，目录名必须为 `inspection`：

   ```bash
   git clone https://github.com/mmm-05610/odoo-equipment-inspection.git custom-addons/inspection
   ```

2. 将 `custom-addons` 和 OCA `manufacture` 目录加入 Odoo 的 `addons_path`，保留官方 addons 路径。
3. 重启 Odoo，开启开发者模式，更新应用列表。
4. 取消应用列表中的默认“应用”筛选，搜索并安装 **Equipment Inspection**。

安装后，功能入口位于 **维护保养 → 检查**。

## 配置

### 人员与检查方案

1. 在用户权限中分配“检查员”或“检查主管”。检查员处理分配给自己的检查，主管管理方案、周期和异常确认。
2. 打开 **维护保养 → 检查 → 检查方案**，填写方案用途、适用设备类别和检查项目。选项项目设置可选答案及其判定；数值项目设置单位、上下限。
3. 打开设备的 **检查** 页签，指定默认检查方案和检查主管；设备的技术员作为默认检查员。

### 周期检查

在设备的 **检查** 页签启用周期检查，设置间隔、单位和首次计划日期。关闭周期会保留配置，不影响手动生成检查。

自动生成还需要管理员启用计划操作 **设备检查：按周期生成**，并确保 Odoo 的 cron 线程运行。本模块默认关闭该计划操作。

计划以首次日期为锚点；月末日期会适配当月天数。错过多期时只补最近一期，不一次补齐全部历史。业务日期时区由参数 `inspection.business_timezone` 控制，默认为 `Asia/Shanghai`。

## 使用

### 执行检查

1. 在设备的 **检查** 页签点击 **生成一次检查**，或由主管通过 **检查 → 批量生成** 创建任务。
2. 检查员打开任务，进入待检查状态，填写必答项目并提交。
3. 全部通过的记录进入“合格”；存在异常的记录进入“待主管确认”。
4. 主管确认异常后，可以创建关联维修请求。再次执行转维修会打开原请求，不重复创建。
5. 维修人员在原生维护保养流程中处理请求，通过“来源检查”返回检查记录。

维修完成不会把历史异常检查改为合格；需要复查时应新建检查。

### 查看历史与计划

- **设备 → 检查页签**：查看最近完成结果、待检查与待确认数量、最近五条记录。
- **检查 → 检查记录**：查找检查单及关联维修。
- **检查 → 检查日历**：查看实际检查和未来周期计划。未来计划只读，浏览日历不会提前创建检查单。
- 检查任务的原生活动显示在设备上，随任务状态和负责人同步。

## 演示数据

[在线演示](https://odoo.maomaokingdom.top/demo) 提供“检查主管”和“检查员”两个登录按钮，可用普通窗口与无痕窗口分别体验。首页可重置数据：会清除所有访客本轮操作并使原登录失效，重置冷却时间为五分钟。

演示场景包含 3 个团队、12 台设备、9 套方案、24 张检查和 8 张维修。建议先体验 P01 的正常检查，再处理 P02 的异常转维修，最后查看 F01 同时存在“最近合格”和“待确认异常”的情况。在线演示关闭自动调度，可手动生成到期检查。

本地首次建库安装时使用 Odoo 的 `--with-demo`，即可加载 manifest 的 `demo` 清单：

| 文件 | 内容 |
| --- | --- |
| `demo/inspection_lang_demo.xml` | 演示账号所需的中文语言 |
| `demo/factory_demo.xml` | 人员、团队、设备、方案和检查项目 |
| `demo/factory_history_demo.xml` | 检查记录、答案与业务状态流转 |
| `demo/factory_repair_demo.xml` | 异常转维修、原生报修与团队改派 |
| `demo/factory_cycle_demo.xml` | 设备周期配置 |

数据通过原生 XML `<record>` 和 `<function>` 加载；提交、确认、转维修调用已有业务方法。`noupdate` 保留升级前的用户修改。日期相对安装日计算，演示阈值不作为实际设备维护标准。

标准安装也会加载依赖模块的 demo。在线站点使用独立空库选择性加载本模块的同一份清单；登录首页与数据库重置服务属于部署环境，不包含在本 addon 中。

## 已知限制

- 一台设备配置一个默认检查方案和一条周期规则。
- 未来日历计划单次查询上限为 400 天、2000 条；超限时提示截断。
- 自定义字段和菜单的英文翻译尚不完整。
- 本项目用于学习与演示，尚未完成生产环境长期运行及压力验证。

## 开发与测试

测试位于 `tests/`，覆盖检查流程、权限、标准快照、异常维修、周期、日历和活动。准备好依赖与数据库连接配置后，可在独立测试库执行：

```bash
odoo -c /path/to/odoo.conf -d inspection_test -i inspection \
  --with-demo --test-enable --test-tags /inspection --stop-after-init
```

工厂演示数据测试需要初始数据，不应在已被访客操作过的演示库中直接运行。

## 问题反馈

请在 [GitHub Issues](https://github.com/mmm-05610/odoo-equipment-inspection/issues) 提交问题，附上 Odoo 与模块版本、操作步骤、预期结果和实际结果；界面问题可附截图。

## 致谢与许可

- [Odoo](https://github.com/odoo/odoo)：维护保养、ORM、视图、活动与任务调度。
- [OCA/manufacture](https://github.com/OCA/manufacture/tree/19.0/quality_control_oca)：通用质量检查模块及其贡献者。

本模块采用 [GNU AGPL v3](LICENSE)，贡献记录见 [Contributors](https://github.com/mmm-05610/odoo-equipment-inspection/graphs/contributors)。
