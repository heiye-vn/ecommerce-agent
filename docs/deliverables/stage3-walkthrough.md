# 阶段 3 交付验证报告：可靠异步任务、重试、补偿与通知

阶段 3 已按规划完成全链路实施，并通过后端静态检查、PostgreSQL + RabbitMQ 真实集成测试、前端 Vitest 与构建检查以及桌面端三视口浏览器验收。

---

## 一、核心交付清单

### 1. 依赖、配置与可回滚迁移
- 依赖与配置：更新 [pyproject.toml](../../backend/pyproject.toml)（新增 `aio-pika>=9.5,<10`）与 [uv.lock](../../backend/uv.lock)，在 [.env.example](../../.env.example) 与 [config.py](../../backend/app/core/config.py) 中新增 `ASYNC_EXECUTION_MODE`（`outbox` / `inline`，默认 `outbox`）及 RabbitMQ 配置校验。
- 数据模型与迁移：在 [reliability.py](../../backend/app/models/reliability.py) 与 [models/__init__.py](../../backend/app/models/__init__.py) 中新增 `Job`、`OutboxEvent`、`Notification` 模型，并通过 Alembic 迁移 [0007_jobs_outbox_notifications.py](../../backend/alembic/versions/0007_jobs_outbox_notifications.py) 落地 `jobs`、`outbox_events` 与 `notifications` 三张表（含店铺复合外键、幂等唯一约束及索引）。

### 2. 任务、Outbox、通知服务与 HTTP API
- **通知服务与接口**：[notifications.py](../../backend/app/services/notifications.py) 提供 `create_notification`、`notify_shop_roles`、`list_notifications`、`mark_notification_read`、`mark_all_notifications_read`；对应 Schema 与路由位于 [schemas/notifications.py](../../backend/app/schemas/notifications.py) 与 [api/notifications.py](../../backend/app/api/notifications.py)。
- **任务 / Outbox 与重试服务**：[jobs.py](../../backend/app/services/jobs.py) 提供 `next_job_no`、`enqueue_job_with_outbox`、`run_loaded_job`、`execute_job_in_transaction`、`record_job_transient_failure`、`retry_dead_letter_job`、`list_jobs`、`get_job_detail`；对应 Schema 与路由位于 [schemas/jobs.py](../../backend/app/schemas/jobs.py) 与 [api/jobs.py](../../backend/app/api/jobs.py)。
- **审批与促销联动**：更新 [services/approvals.py](../../backend/app/services/approvals.py)、[api/approvals.py](../../backend/app/api/approvals.py)、[schemas/approvals.py](../../backend/app/schemas/approvals.py)、[services/promotions.py](../../backend/app/services/promotions.py) 与 [main.py](../../backend/app/main.py)，实现审批通过按 `ASYNC_EXECUTION_MODE` 走 Outbox 异步队列（`queued` 并返回 `job_id` / `job_no`）或本地同步直执行，并衔接促销生效/结束/错过窗口通知。

### 3. RabbitMQ Topology 与三个独立 Worker 进程
- [topology.py](../../backend/app/workers/topology.py)：`TopologyConfig`、`DeclaredTopology`、`connect_rabbitmq`、`declare_topology`（声明主交换机、死信交换机 `DLX`、主队列与死信队列 `DLQ`）。
- [outbox_publisher.py](../../backend/app/workers/outbox_publisher.py)：`publish_pending_batch` 与独立进程 `run_outbox_publisher`（持久化消息 + Publisher Confirms）。
- [consumer.py](../../backend/app/workers/consumer.py)：`handle_incoming_message` 与独立进程 `run_consumer`（数据库事务提交后手动 ACK、瞬态异常指数退避重投递及超次进入 DLQ）。
- [scheduler.py](../../backend/app/workers/scheduler.py)：`scan_due_promotions` 与独立进程 `run_scheduler`（到点激活、结束及错过窗口幂等补偿）。

### 4. 前端页面、导航壳与文档同步
- **任务中心**：新增 [tasks/index.tsx](../../frontend/src/pages/tasks/index.tsx) 与 [tasks/index.module.css](../../frontend/src/pages/tasks/index.module.css)，接入 `/tasks` 与 `/tasks/:jobId`（任务状态筛选、详情抽屉/面板、死信人工重试）。
- **通知中心与顶栏未读角标**：新增 [notifications/index.tsx](../../frontend/src/pages/notifications/index.tsx) 与 [notifications/index.module.css](../../frontend/src/pages/notifications/index.module.css)，接入 `/notifications`，并更新 [App.tsx](../../frontend/src/App.tsx)、[App.module.css](../../frontend/src/App.module.css)、[router.tsx](../../frontend/src/router.tsx)（仅订阅缓存中通知 Query，无多余首屏请求且已修复渲染期同步 `setState` 警告）。
- **现有页面与查询集成**：更新 [queries.ts](../../frontend/src/api/queries.ts)、[approvals/index.tsx](../../frontend/src/pages/approvals/index.tsx) 与 [promotions/index.tsx](../../frontend/src/pages/promotions/index.tsx)，支持异步入队状态跳转到任务中心与阶段 3 审计事件标签展示。
- **使用文档**：更新 [README.md](../../README.md)，补充阶段 2/3 项目状态、`scripts.seed_stage2`、3 个阶段 3 Worker 启动命令以及测试环境说明。

---

## 二、自动化测试与构建验证结果

> [!NOTE]
> 后端全量测试在阶段 3 后端代码修改完成后运行并观察通过；随后仅修改了前端 [router.tsx](../../frontend/src/router.tsx) 与 [README.md](../../README.md)，并在最后一次前端修改后完整复验了前端测试与构建。

| 检查项 | 执行命令 | 实测结果 |
| --- | --- | --- |
| 后端风格检查 | `cd backend && uv run ruff check app tests scripts` | **All checks passed!** |
| 后端静态类型检查 | `cd backend && uv run mypy app` | **Success: no issues found in 54 source files** |
| 后端测试套件（含 PG + RabbitMQ 集成测试） | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest` | **38 passed in 16.22s**（新增 [test_stage3_reliability.py](../../backend/tests/integration/test_stage3_reliability.py) 并更新 [test_database.py](../../backend/tests/integration/test_database.py) 与 [test_promotions_approvals.py](../../backend/tests/integration/test_promotions_approvals.py)） |
| 本地数据库迁移 | `cd backend && uv run alembic upgrade head` | **Exit 0**（应用至 `0007_jobs_outbox_notifications`） |
| 前端 Vitest 自动化测试（最终修改后实测） | `cd frontend && npm test -- --run` | **7 test files, 28 passed (28)**（含 [tasks_notifications.test.tsx](../../frontend/tests/tasks_notifications.test.tsx)，无 React `Cannot update a component` 警告） |
| 前端 TypeScript 与生产构建（最终修改后实测） | `cd frontend && npm run build` | **`tsc && vite build` 成功（0 错误，built in 246ms）** |

---

## 三、浏览器接口核验与桌面端三视口验收

临时在 `127.0.0.1:7629`（后端）与 `127.0.0.1:7639`（前端 Vite 预览代理）启动服务后，由主机验证的浏览器实测结果如下（验收完成后已终止两个临时进程）：

- **OpenAPI 路由核验**（`http://127.0.0.1:7629/openapi.json`）：返回 `200`，共注册 `26` 条路径，阶段 3 全部 6 条接口均存在：
  - `GET /api/shops/{shop_id}/jobs`
  - `GET /api/shops/{shop_id}/jobs/{job_id}`
  - `POST /api/shops/{shop_id}/jobs/{job_id}/retry`
  - `GET /api/shops/{shop_id}/notifications`
  - `POST /api/shops/{shop_id}/notifications/{notification_id}/read`
  - `POST /api/shops/{shop_id}/notifications/read-all`
- **就绪探针核验**（`http://127.0.0.1:7639/api/health/ready`）：返回 `200 OK`，响应 JSON 为 `{"status":"ok","service":"commerce-agent-backend","checks":{"postgresql":"ok","redis":"ok","rabbitmq":"ok"}}`。
- **受保护路由重定向**：未登录状态下访问 `/tasks` 与 `/notifications` 均自动重定向至 `/login`，页面渲染正常，无控制台未捕获 JS 错误。
- **桌面端三视口布局核验**（`/login` 页面依次检测 `1440x900`、`1920x1080`、`2560x1440`）：三种桌面视口下 `html` 与 `body` 边界尺寸均与视口一致，横向与纵向滚轮滚动位移均为 `0`，无元素截断/重叠或控制台异常。