# Relay

Relay 面向汽车供应链交付计划采集。阶段、依赖和验收条件见[项目工作地图](docs/工作地图.md)。架构与字段映射见[技术方案](docs/architecture/Relay-汽车供应链数据采集平台-技术方案.md)，前期预算见[工作量与费用评估](docs/commercial/Relay-项目工作量与费用评估.md)。合同 PDF 和 Word 草稿分别保存在 `docs/commercial/contracts/` 和其 `drafts/` 子目录。

## 本地容器开发

需要 Docker 和 Compose。复制 `.env.example` 为 `.env`，填入本地数据库密码。在仓库根目录运行：

```sh
docker compose --env-file .env -f deployments/docker-compose.yml up --build --wait
```

默认入口为 `http://127.0.0.1:8080`。通过 `/api/v1/health/live` 检查 API 进程，通过 `/api/v1/health/ready` 检查数据库连接和迁移基线。Compose 由 `migrate` 容器先执行迁移，再启动 API。该配置用于本地开发，客户部署的 HTTPS、备份和监控安排见工作地图。

首页是「邮件与附件」工作台。表格、附件和重试操作使用演示数据，系统状态读取本机 API。康尼新能源 Logo 来自[客户官网](https://www.knn-nj.com/skin/images/logo.png)，在本地镜像中随前端提供。

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
