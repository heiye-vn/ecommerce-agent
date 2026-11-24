# CommerceAgent · 电商运营 Agent

CommerceAgent 是一个仅面向桌面端的可控执行型电商运营 Agent 项目。它的目标不是实现完整电商平台，而是打通一条可演示、可验证的业务闭环：

```text
自然语言指令 → 业务数据查询 → 执行方案生成 → 风险与权限校验
→ 人工审批 → 业务执行 → 失败重试与补偿 → 全程审计
```

## 项目状态

当前仓库已完成**阶段 3（可靠异步任务、重试、补偿与通知）**核心开发，包含：

- 产品定位、业务规则、核心表结构与分阶段计划的[产品设计](docs/product-design.md)。
- [各阶段实施计划与交付验证报告](docs/deliverables/)。
- 视觉规范 [DESIGN.md](DESIGN.md)。
- 68 张桌面端 UI 原型图与阅读索引，见 [prototypes/README.md](prototypes/README.md)。
- FastAPI 后端骨架与健康检查接口（含 PostgreSQL、Redis、RabbitMQ 就绪探测）。
- PostgreSQL 异步连接与组织、用户、店铺、成员授权表的初始迁移。
- React + Ant Design 桌面工作台外壳。
- 账号登录、会话恢复与退出，以及按服务端授权读取店铺。
- 商品、SKU、库存、价格历史和订单的数据库迁移，以及固定沙箱样例初始化脚本。
- 商品运营页：按当前授权店铺查询 SKU、售价和库存，支持搜索、筛选与分页。
- 订单售后页：按当前授权店铺查询订单和商品明细，支持订单号、SKU、状态及支付日期筛选。
- **阶段 2**：促销管理、审批工作台、幂等与行锁并发排期、审计日志、Agent 多轮追问与显式确认闭环。
- **阶段 3**：PostgreSQL Outbox + RabbitMQ 可靠异步任务（`jobs` / `outbox_events` / `notifications`）、独立 Outbox Publisher / Consumer / Scheduler 进程、指数退避重试与 DLX/DLQ 死信人工重试、任务中心（`/tasks`）与通知中心（`/notifications`）。
- PostgreSQL(pgvector)、Redis 与 RabbitMQ 的本地 Compose 编排。

商品详情、库存流水、调价记录、履约、退款、知识库与活动复盘留待阶段 4 及后续阶段开发。

## 核心设计

- **LLM 不直接修改数据库**：模型负责理解意图、分析数据与生成结构化方案。
- **Agent 工具受控**：所有业务访问通过权限受控的服务层完成。
- **高风险操作必须审批**：促销、调价、退款等操作提交后进入人工审批。
- **执行前重新校验**：审批通过后仍会复核价格、库存、SKU 状态、活动时段与权限。
- **事实数据来自数据库**：销量、库存、订单和价格由查询结果计算，不由模型编造。
- **全程可追踪**：工具调用、审批、执行状态和审计记录关联到同一任务与资源。

## 技术规划

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI、SQLAlchemy 2.x、Pydantic、pytest |
| 数据库 | PostgreSQL、Alembic |
| 异步任务 | RabbitMQ、PostgreSQL Outbox、Worker、Scheduler |
| Agent | LangGraph、LangChain、OpenAI 兼容接口 |
| 前端 | React、TypeScript、Vite、Ant Design 5、Vitest |

双模型渠道（DashScope / OpenAI 中转站）仅通过后端 `.env` 配置切换，前端不提供模型切换控件。

## 实施路线

1. **阶段 0–1**：工程骨架、组织权限、商品、库存、订单基础数据。
2. **阶段 2**：促销、审批、幂等、审计和基础 Agent 闭环。
3. **阶段 3**：可靠异步任务、重试、补偿与通知。
4. **阶段 4–5**：退款、调价、经营分析、知识库与活动复盘。
5. **阶段 6**：故障模拟、评测、部署与演示优化。

首版重点先完成促销闭环：运营输入、Agent 追问、方案生成、人工审批、执行复核、排期和审计。业务数据使用合成数据初始化到 PostgreSQL，不连接真实店铺、支付或物流。

## 目录结构

```text
.
├── AGENTS.md
├── DESIGN.md
├── README.md
├── docs/
│   ├── deliverables/
│   └── product-design.md
└── prototypes/
    ├── README.md
    ├── design-md.html
    └── png/
```

## 参与开发

- 修改前先阅读 [AGENTS.md](AGENTS.md)、[docs/product-design.md](docs/product-design.md) 和 [DESIGN.md](DESIGN.md)。
- 后端与 Agent 不允许绕过服务层直接修改业务表。
- 后端测试放在 `backend/tests/`，前端测试放在 `frontend/tests/`。
- 不要提交 `.env`、密钥、本地日志、`.DS_Store` 或 `.playwright-mcp/`。

## 本地启动

1. 首次启动时准备环境变量（已有 `.env` 则不要覆盖）：

```bash
cp .env.example .env
```

将 `openssl rand -hex 32` 生成的独立随机值填入 `.env` 的 `AUTH_JWT_SECRET`。刷新 Cookie 默认启用 `Secure`；请使用 `localhost` 或 HTTPS，只有在受信任的本地 HTTP 环境确实需要时才设置 `AUTH_COOKIE_SECURE=false`。

2. 启动本地基础设施（宿主端口均为 76xx，避免与常用默认端口冲突）：

```bash
docker compose up -d
```

| 服务 | 宿主端口 |
| --- | --- |
| PostgreSQL | `7600` |
| Redis | `7601` |
| RabbitMQ | `7602` |
| RabbitMQ 管理台 | `7603` |
| 后端 API | `7620` |
| 前端开发服务 | `7630` |
| 前端预览服务 | `7631` |

3. 迁移数据库、初始化演示账号并启动后端：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run python -m scripts.seed
uv run python -m scripts.seed_catalog
uv run python -m scripts.seed_stage2   # 可选：初始化阶段 2 促销与审批演示数据
uv run uvicorn app.main:app --host 127.0.0.1 --port 7620
```

初始化命令只允许连接本机 `7600` 端口的 `commerce_agent` 数据库，只为 `ops.demo` 分配「轻食旗舰店」的运营角色。初始密码保存在仓库根目录被 Git 忽略的 `.env.demo-login` 中。重复执行不会重置已有账号的密码。

`scripts.seed_catalog` 是单独的本地开发命令，只向上述演示店铺写入合成数据，不创建账号，也不提供 UI 或 API 入口。店铺已有 `SKU-001` 时跳过，不覆盖价格、库存或订单。首次执行会生成 `SKU-001`（售价 ¥199.00、可售库存 300 件）和 7 笔已支付订单。以 `Asia/Shanghai` 的 2026-11-21 为固定演示日，统计 11 月 14 日 00:00（含）至 11 月 21 日 00:00（不含）可得 120 件；该窗口不随系统日期滚动。

登录后可在「订单售后」查看这 7 笔订单。销量只读接口 `GET /api/shops/{shop_id}/sales?sku_code=SKU-001&as_of=2026-11-21` 需要当前店铺授权；`as_of` 为不计入统计的上海时区自然日，接口返回此前 7 个完整自然日的支付件数及订单数，不抵扣后续退款。

在「Agent 对话」发送 `查一下 SKU-001 最近 7 天销量` 可用同一口径查询。当前支持单个沙箱 SKU（如 `SKU-001`，输入大小写不敏感），使用 2026-11-21 作为固定业务日。模型根据输入识别意图，销量由服务层计算。使用前在后端 `.env` 中为选定的 `LLM_PROVIDER` 配好 `BASE_URL`、`API_KEY` 和 `DEFAULT_MODEL`。请勿在问题中输入敏感信息。

也可点击「试用促销方案预览」或直接发送促销排期需求（如 `对 SKU-001 在 11 月 22 日开始做 8 折促销`），在「Agent 对话」内完成多轮追问、显式确认与审批提单闭环。服务层会按售价和折扣核算促销价、校验库存门槛（100 件）与活动时段冲突，并按上海时间展示活动区间。

4. 启动阶段 3 异步任务进程（当 `.env` 中 `ASYNC_EXECUTION_MODE=outbox` 时，默认开启）：

在 `.env` 中通过 `ASYNC_EXECUTION_MODE` 控制审批通过后的执行方式：`outbox`（默认）会在同一事务写入任务与 Outbox 事件，由 RabbitMQ 异步投递执行；设为 `inline` 则在本地请求内同步直执行。使用 `outbox` 模式时，需在独立终端分别启动以下 3 个后台进程：

```bash
cd backend && uv run python -m app.workers.outbox_publisher
cd backend && uv run python -m app.workers.consumer
cd backend && uv run python -m app.workers.scheduler
```

5. 启动前端：

```bash
cd frontend
npm install
npm run dev
```

后端健康检查：<http://localhost:7620/api/health>（就绪检查：<http://localhost:7620/api/health/ready>）；登录页：<http://localhost:7630/login>。登录后进入工作台；重新打开页面时可通过 HttpOnly Cookie 恢复会话。开发和预览服务器通过同源 `/api` 代理访问后端；如使用其他后端端口，启动前端时设置 `API_PROXY_TARGET`。

## 测试

```bash
cd backend && uv run pytest
cd frontend && npm test -- --run
```

后端集成测试需要 Docker，由 Testcontainers 启动独立 PostgreSQL，不会修改 Compose 中的数据库；其中阶段 3 集成测试（`tests/integration/test_stage3_reliability.py`）还会连接本机 Compose 的 RabbitMQ（`localhost:7602`），使用独立随机前缀的测试 Exchange/Queue 并在测试结束后自动清理。单独运行单元测试用 `uv run pytest tests/unit`。若本地仅因无法拉取 Ryuk 清理镜像而无法启动测试，可用 `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest`；测试正常结束时仍会移除临时容器。
