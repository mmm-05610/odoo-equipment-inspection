# 部署与访客重置

本仓库部署到独立目录 `/opt/odoo-inspection`，Compose 项目固定为 `inspection_public`。Odoo 和 PostgreSQL 基础镜像在 Dockerfile 中固定 SHA256；OCA 依赖固定 Git 提交。

## 首次准备

```bash
./scripts/fetch-deps.sh
docker build -f deploy/Postgres.Dockerfile -t inspection-postgres:15 .
docker build -f deploy/Dockerfile -t inspection-public:19.0.1.3 .
sudo python3 deploy/configure.py https://odoo.maomaokingdom.top
sudo bash deploy/bootstrap.sh
sudo bash deploy/reset.sh
```

配置脚本只在首次执行时生成 `.runtime/secrets.env` 和 `odoo.conf`，文件不进入 Git。请保管 `.runtime`。再次配置会拒绝覆盖已有凭据。

该服务器的 Caddy 在 Docker 中，所以 Odoo 绑定 `172.17.0.1:8076`，重置入口绑定 `172.17.0.1:8092`，不监听公网网卡。换服务器时应核实 Docker bridge 地址并调整配置。PostgreSQL 不映射宿主端口。不要直接暴露 8076/8092，入口由 Caddy 的 HTTPS 提供。

`bootstrap.sh` 创建非超级用户 `odoo`，安装一个不含 demo 的 `inspection_template`，禁止模板库接受连接。模板仅在初次安装时建立，已存在时不会覆盖。源码升级涉及数据库结构或 demo 时，要先备份再单独重建模板，不要只换镜像后假定模板自动更新。

## 访客入口

`deploy/demo.html` 显示两套账号、演示步骤、共享库提示和重置按钮。`reset_server.py` 使用 Python 标准库，无额外框架依赖；它以专用系统用户运行。

服务器需创建系统用户 `inspection-reset`，状态目录 `/var/lib/inspection-reset` 归该用户所有。`deploy/inspection-reset.service` 安装到 systemd。代码、脚本及父目录均由 root 管理，不允许该用户修改。sudoers **仅**授权该用户无参数执行：

```sudoers
Defaults:inspection-reset !requiretty
inspection-reset ALL=(root) NOPASSWD: /opt/odoo-inspection/deploy/reset.sh ""
```

此服务器为 systemd 较旧版本，`RestrictAddressFamilies` 等 seccomp 选项会隐式启用 `NoNewPrivileges`，阻止固定 sudo 命令；提供的 unit 不启用这类选项，仍保留专用用户、只读系统目录、私有临时目录和严格的无参数 sudo 白名单。

服务不加入 Docker 用户组、不挂 Docker socket、不知道数据库密码。浏览器不能指定数据库名、文件路径或命令。重置需要同源 POST、请求令牌、明确确认；单任务互斥，每次触发后冷却 300 秒。状态及冷却时间持久化到专用状态目录，重启不会立即解除冷却。

Caddy 使用 `deploy/Caddyfile.fragment` 增加此子域名，并阻止 `/web/database` 管理入口及本演示不使用的 `/xmlrpc`、`/jsonrpc` 外部 API（网页自身的 `/web/dataset` 不受影响）。保留同服务器其他站点配置，先 `caddy validate`，再 reload。

## 重置过程

1. 文件锁保证任何入口都不会并发重置。
2. 从只读空业务模板克隆 `inspection_next`，复制模板 filestore。
3. 在候选库通过实际业务动作生成当天 demo，确认 12 台设备 / 3 个团队 / 9 套方案 / 24 张检查 / 8 张维修。
4. 管理员与其他演示人员密码随机化，保留公开主管、检查员账号。关闭外发邮件服务器、计划任务，设置新会话密钥。
5. 短暂停止 web，将当前库留为禁止连接的 `inspection_previous`，候选库切换为 `inspection_demo`，同步 filestore。
6. 启动并检查服务；切换后的失败会尝试恢复上一套库和 filestore，并重新允许恢复库连接。访客看到成功后重新登录。

只保留最近一套 previous，不无限积累数据库。每次重置会删除更早的 previous。该机制用于共享演示，不应接到真实业务库。

## 运维

```bash
sudo systemctl status inspection-reset
sudo journalctl -u inspection-reset --no-pager -n 80
sudo bash /opt/odoo-inspection/deploy/reset.sh
```

后台 cron 在演示环境关闭。用户可主动生成到期检查，避免重置完成后初始数据自行变化。日历的未来计划仍可正常预览。

HTTP 保护测试：`python3 -m unittest discover -s deploy -p 'test_reset_server.py'`。它使用假的重置任务验证接口边界；实际恢复必须另外验证网页重置、账号重登、数据回到初始数量。

官方部署依据：[Odoo 19 系统配置](https://www.odoo.com/documentation/19.0/administration/on_premise/deploy.html)。本项目采用单库筛选、禁用数据库管理、反向代理与非超级用户数据库角色；仍不宣称完成生产级安全审计。
