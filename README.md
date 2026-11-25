# Relay

Relay 面向汽车供应链交付计划采集。阶段、依赖和验收条件见[项目工作地图](docs/工作地图.md)。架构与字段映射见[技术方案](docs/architecture/Relay-汽车供应链数据采集平台-技术方案.md)，前期预算见[工作量与费用评估](docs/commercial/Relay-项目工作量与费用评估.md)。合同 PDF 和 Word 草稿分别保存在 `docs/commercial/contracts/` 和其 `drafts/` 子目录。

## 本地容器开发

需要 Docker 和 Compose。复制 `.env.example` 为 `.env`，填入本地数据库密码。在仓库根目录运行：

```sh
docker compose --env-file .env -f deployments/docker-compose.yml up --build --wait
```

默认入口为 `http://127.0.0.1:8080`。通过 `/api/v1/health/live` 检查 API 进程，通过 `/api/v1/health/ready` 检查数据库连接和迁移基线。Compose 由 `migrate` 容器先执行迁移，再启动 API。该配置用于本地开发，客户部署的 HTTPS、备份和监控安排见工作地图。

登录后默认进入「仪表盘」。首页显示按北京时间统计的今日收件、完成解析的附件、待处理邮件、交付草稿、近七天收件量及最近收件。待跟进条目可直接打开对应的处理队列。

管理员可按引导配置业务邮箱、测试连接和启动采集，查询用户可以查看同步状态和归档记录。「待处理」汇总采集及解析异常、待核对的原值和解析队列。「收件档案」支持搜索主题、发件人和附件名，进入详情可查看原始数量坐标及下载原件。「系统状态」显示 API 与数据库、采集和解析进程心跳、邮箱最近同步、解析任务统计及最近一份归档原件的完整性校验。康尼新能源 Logo 来自[客户官网](https://www.knn-nj.com/skin/images/logo.png)，登录页充电场景来自[官网首页横幅](https://www.knn-nj.com/UploadFiles/2025/2025091210160744120.jpg)。两份素材均随本地前端镜像提供。

运行状态页每 30 秒刷新，也可手动重新检查。后台进程超过 75 秒未上报心跳时显示「心跳超时」。邮箱状态依据最近一次只读同步结果，附件归档使用最近一份原件进行完整性检查。

## 物料匹配和德科交付草稿

管理员从「收件档案」打开已解析的德科附件，选择「生成交付草稿」。在「待核对」里可就地补录映射，也可以在「物料匹配」统一维护。点击需求数量可查看来源单元格。对已匹配且有明确日期的需求，可录入有依据的发货日、运输方式及可选的人工 ETA。

「下载草稿」生成包含「给生产」和「需求与来源」的 XLSX。「给生产」的发货数量来自已录入发货安排的记录，其余需求可在「需求与来源」核对。查询用户可查看和下载已生成的草稿。其他客户的生成工作见[工作地图](docs/工作地图.md#当前工作包)。

## 登录与用户管理

已有本地环境可使用 `admin` 登录，首次登录使用本机 `.env` 中的 `RELAY_INITIAL_ADMIN_PASSWORD`，随后按页面提示修改。改密成功后可从 `.env` 删除临时密码。查询用户可以查看归档邮件、原始数量记录和下载原件。管理员可以配置邮箱、创建用户、修改角色、停用或删除其他账号和重置临时密码。删除后撤销该账号的所有会话，历史交付记录保留，用户名可以用于创建新账号。

全新部署初始化一次管理员。命令在终端安全输入临时密码：

```sh
docker compose --env-file .env -f deployments/docker-compose.yml exec api python -m relay.auth.bootstrap
```

为邮箱授权码生成并保管一把固定的 Fernet 密钥。将以下命令生成的值填写到 `.env` 的 `RELAY_MAIL_ENCRYPTION_KEY` 后重建容器。恢复数据库备份时要使用同一密钥：

```sh
uv run --locked python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

本地入口仅监听回环地址。客户环境启用 HTTPS，并设置 `RELAY_COOKIE_SECURE=true`。

## 邮箱配置和采集

管理员进入「邮箱配置」，填写邮箱地址、客户端授权码、文件夹和历史采集起始日期，依次点击「保存配置」「测试连接」「启动采集」。支持 263 企业邮箱和网易 163 个人邮箱，均使用 IMAP TLS 993。输入 `@163.com` 地址时，页面自动选择 `imap.163.com`。163 账号需要在网易邮箱启用 IMAP 并使用客户端授权码，Relay 登录后会发送 IMAP 客户端标识。测试收件箱可填写 `INBOX`。

目前一次采集一个邮箱。切换邮箱地址或 IMAP 服务器时需要填写新授权码。保存新配置会暂停采集，重新测试后再启用。已归档邮件和各邮箱的同步游标继续保留。暂停操作会停止后续邮件读取，正在读取的单封邮件可以收尾。

Mail Worker 使用只读 IMAP，以 UID 游标增量同步。服务器返回的 UIDVALIDITY 变化时暂停该文件夹的同步，等待核对旧游标。Parser Worker 支持 Vitesco、DRX、JLR DELFOR、JLR DELJIT 和 Kroschu 五份样本对应的结构。JLR 和 Kroschu 的六位日期原样保留，等客户确认世纪规则后再规范化。归档卷 `attachment-archive` 和数据库卷 `postgres-data` 应成对备份与恢复。

可以用本地 `.eml` 重放验证，无需连接邮箱。以下示例在仓库根目录运行，替换邮件文件路径即可，同一 UID 重放不会新增接收记录：

```sh
docker compose --env-file .env -f deployments/docker-compose.yml run --rm --no-deps \
  -v "/absolute/path/to/forecast.eml:/tmp/forecast.eml:ro" mail-worker \
  python -m relay.workers.replay /tmp/forecast.eml --uid 1
```

首次回放后在「收件档案」刷新即可查看。每封不同的本地重放邮件使用一个新的 UID。

## 宿主机开发

后端需要 Python 3.12、uv 和可访问的 PostgreSQL。使用环境变量设置 `RELAY_DB_HOST`、`RELAY_DB_PORT`、`RELAY_DB_NAME`、`RELAY_DB_USER`、`RELAY_DB_PASSWORD`，也可填写根目录 `.env`。主机、端口和账号默认是 `127.0.0.1:5432`、`relay`，数据库默认是 `relay`。

```sh
uv sync --locked
uv run alembic upgrade head
uv run uvicorn relay.api.app:app --reload
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src/relay
```

前端需要 Node.js 24。开发服务器把同源 API 请求转发到 `http://127.0.0.1:8000`。可以用 `RELAY_API_ORIGIN` 覆盖。`npm run start` 启动生产构建，跨服务路由由 Compose 的 Nginx 提供。

```sh
cd web
npm ci
npm run dev
npm run typecheck
npm run build
npm run start
```

后端的 PostgreSQL 集成检查需设置 `RELAY_TEST_DB=1`，并连接已迁移的测试库。原始邮件附件放在 `samples/edi/raw/`，参考表放在 `samples/edi/reference/`。本地样本、合同和检查用数据与部署镜像隔离。

## Vitesco 解析核对

可直接在本地读取归档样本，查看解析出的物料块和原始数量记录数：

```sh
uv run --locked python - <<'PY'
from pathlib import Path
from relay.parsers.vitesco import parse_vitesco

sample = Path("samples/edi/raw/Vitesco_DELFOR_24_261030060447.xlsx")
result = parse_vitesco(sample)
print(len(result.blocks), sum(len(block.quantities) for block in result.blocks))
PY
```

结果应为 `1 88`。自动测试用独立构造的样本验证零值、日期区间、未知代码、坐标和公式拒绝行为。
