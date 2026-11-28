# 阶段 4 交付验证报告：退款、订单售后 Agent 与库存并发

阶段 4 已按 [阶段 4 实施计划](./stage4-implementation-plan.md) 完成后端数据层、退款与审批执行链路、库存并发控制、订单售后 Agent 与前端工作台全部交付，并已更新 [README.md](../../README.md)；所有自动化测试、静态检查与桌面端浏览器基线核查均已通过。

---

## 一、核心交付清单

### 1. 数据模型与可回滚迁移（`0008`）
- **退款模型**：新增 [refund.py](../../backend/app/models/refund.py)（`Refund`），引入状态检查约束（`pending_approval`、`approved`、`processing`、`succeeded`、`rejected`、`cancelled`、`failed`）、正数金额约束、复合外键约束 `(shop_id, order_id) -> orders(shop_id, id)` 以及数据库级部分唯一索引 `uq_refunds_active_order`（阻断同一订单并发生成多笔在途退款）。
- **订单与库存扩展**：更新 [catalog.py](../../backend/app/models/catalog.py)、[reliability.py](../../backend/app/models/reliability.py) 与 [models/__init__.py](../../backend/app/models/__init__.py)，在 `Order` 新增 `fulfillment_status` 与 `exception_type`，在 `InventoryMovement` 扩展 `shop_id`、前后可用/预占快照、`actor_id`、`note` 与 `ck_inventory_movements_type`（含 `opening_balance`），并扩展 `ck_jobs_type`（含 `refund_execute`）与 `ck_notifications_category`（含 `refund`）。
- **Alembic 迁移与演练数据**：新增可回滚迁移 [0008_refunds_and_inventory_ops.py](../../backend/alembic/versions/0008_refunds_and_inventory_ops.py)，以及幂等本地初始化脚本 [services/seed_stage4.py](../../backend/app/services/seed_stage4.py) 与 [scripts/seed_stage4.py](../../backend/scripts/seed_stage4.py)（初始化 `SKU-005` 硅胶杯盖 18 件低库存预警样本与 `ORD-20261111-01`、`ORD-20261111-02` 异常订单，日期位于 7 天销量窗口之外以保持 `120` 件基线）。

### 2. 退款审批、并发控制与异步队列泛化
- **退款服务与路由**：新增 [schemas/refunds.py](../../backend/app/schemas/refunds.py)、[services/refunds.py](../../backend/app/services/refunds.py) 与 [api/refunds.py](../../backend/app/api/refunds.py)，扩展 [schemas/orders.py](../../backend/app/schemas/orders.py) 与 [services/orders.py](../../backend/app/services/orders.py)（提供 `EXCEPTION_ADVICE_MAP`、`compute_order_refund_eligibility` 及聚合退款资格与记录的 `OrderDetail`）。
- **审批、任务与 RabbitMQ Consumer 泛化**：更新 [services/approvals.py](../../backend/app/services/approvals.py)、[services/jobs.py](../../backend/app/services/jobs.py)、[schemas/jobs.py](../../backend/app/schemas/jobs.py) 与 [schemas/notifications.py](../../backend/app/schemas/notifications.py)，支持 `resource_type in ("promotion", "refund")`，并在 `list_jobs` / `get_job_detail` 按资源类型隔离关联表 `outerjoin`；更新 [topology.py](../../backend/app/workers/topology.py)、[outbox_publisher.py](../../backend/app/workers/outbox_publisher.py) 与 [consumer.py](../../backend/app/workers/consumer.py)，通过 `TopologyConfig.production()` 增加 `commerce.jobs.refund` 与 `commerce.jobs.refund.dlq`，由既有 `app.workers.consumer` 进程并发消费两条队列，不额外增加第 4 个 Worker。
- **库存并发控制服务与路由**：新增 [schemas/inventory.py](../../backend/app/schemas/inventory.py)、[services/inventory.py](../../backend/app/services/inventory.py) 与 [api/inventory.py](../../backend/app/api/inventory.py)，并在 [main.py](../../backend/app/main.py) 挂载路由；事务内通过 `FOR UPDATE` 行锁、`expected_version` 乐观锁（冲突返回 `409 INVENTORY_VERSION_CONFLICT`）、`Idempotency-Key` 与不可变流水保障并发一致性。

### 3. 订单售后 Agent 升级
- 更新 [schemas/agent.py](../../backend/app/schemas/agent.py)、[services/promotion_preview.py](../../backend/app/services/promotion_preview.py)、[agent/llm_gateway.py](../../backend/app/agent/llm_gateway.py)、[agent/tools.py](../../backend/app/agent/tools.py)（新增 `query_order_tool`、`preview_refund_tool`、`submit_refund_approval_tool`）、[services/agent.py](../../backend/app/services/agent.py)、[agent/graph.py](../../backend/app/agent/graph.py) 与 [api/agent.py](../../backend/app/api/agent.py)，并新增 [services/refund_preview.py](../../backend/app/services/refund_preview.py)。
- 支持只读查单与异常订单建议输出、退款诉求缺失金额/原因时的多轮追问（无写库）以及 `confirm_plan` 显式确认后幂等提交退款审批单。

### 4. 前端桌面端页面扩展
- **API 查询与路由**：更新 [queries.ts](../../frontend/src/api/queries.ts) 与 [router.tsx](../../frontend/src/router.tsx)，新增 `/orders/refunds/:refundId` 路由并将店铺角色与用户 ID 传递至 `OrdersPage`。
- **工作台页面**：
  - 商品运营 [catalog/index.tsx](../../frontend/src/pages/catalog/index.tsx) & [catalog/index.module.css](../../frontend/src/pages/catalog/index.module.css)：新增「库存与流水」Tab、4 格指标带、流水筛选、低库存预警侧栏与乐观锁操作弹窗；
  - 订单售后 [orders/index.tsx](../../frontend/src/pages/orders/index.tsx) & [orders/index.module.css](../../frontend/src/pages/orders/index.module.css)：新增「异常订单」与「退款记录」Tab、订单详情退款资格校验与申请弹窗，以及退款详情页（含申请人撤回）；
  - 审批/任务/通知/Agent：更新 [approvals/index.tsx](../../frontend/src/pages/approvals/index.tsx)、[tasks/index.tsx](../../frontend/src/pages/tasks/index.tsx)、[notifications/index.tsx](../../frontend/src/pages/notifications/index.tsx)、[agent/index.tsx](../../frontend/src/pages/agent/index.tsx) & [agent/index.module.css](../../frontend/src/pages/agent/index.module.css)。

---

## 二、自动化测试与构建验证结果

> [!NOTE]
> 以下所有后端、前端检查命令均在各自模块最终代码定稿后完整重跑并直接观察通过。

| 检查项 | 执行命令 | 实测结果 |
| --- | --- | --- |
| 后端风格检查 | `cd backend && uv run ruff check app tests scripts` | **All checks passed!**（Exit 0） |
| 后端静态类型检查 | `cd backend && uv run mypy app` | **Success: no issues found in 63 source files** |
| 后端全量测试（含 PG + RabbitMQ 集成测试） | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest` | **42 passed, 17 warnings in 22.27s**（覆盖 [test_database.py](../../backend/tests/integration/test_database.py)、[test_stage4_refunds_inventory.py](../../backend/tests/integration/test_stage4_refunds_inventory.py)、[test_stage4_refund_agent.py](../../backend/tests/agent/test_stage4_refund_agent.py) 与 [test_agent_gateway.py](../../backend/tests/unit/test_agent_gateway.py) 等；17 条 warning 均来自第三方 `langsmith` 包的 Python 3.16 `asyncio.iscoroutinefunction` 弃用提示） |
| 本地数据库迁移与种子脚本 | `cd backend && uv run alembic upgrade head`<br>`cd backend && uv run python -m scripts.seed_stage4` | 本机数据库已迁移至 `0008_refunds_and_inventory_ops (head)`；`seed_stage4` 连续执行两次（首次写入阶段 4 演示数据、二次幂等跳过） |
| 前端 Vitest 单元测试 | `cd frontend && npm test -- --run` | **Test Files 8 passed (8)，Tests 32 passed (32)**（含新增 [stage4_refunds_inventory.test.tsx](../../frontend/tests/stage4_refunds_inventory.test.tsx) 4 条用例，无 React `Warning:` / `setState` 报错） |
| 前端 TypeScript 与生产构建 | `cd frontend && npm run build` | **`tsc && vite build` 成功（0 错误，dist 构建耗时 252ms）** |

---

## 三、复用本地服务的桌面端浏览器核验

遵循不重复启停用户既有进程的原则，直接复用本机运行中的 `http://127.0.0.1:7630`（Vite 前端开发服务）与 `http://127.0.0.1:7620`（当前尚处于阶段 3 代码版本的 `uvicorn` 后端进程）完成只读浏览器核查（经主机 `BROWSER_HOST_FACTS` 确认访问了 `http://127.0.0.1:7630` 与 `http://127.0.0.1:7620`、`blocked_origins: none`，并在最终 `1440x900` 视口人工查验截图）：

- **未登录受保护路由校验**：
  - 访问 `http://127.0.0.1:7630/orders/refunds/1` 与 `http://127.0.0.1:7630/orders` 均返回 `200 OK` 并自动重定向至 `http://127.0.0.1:7630/login`；
  - 仅出现预期的未登录态刷新检查请求 `POST http://127.0.0.1:7630/api/auth/refresh -> 401 Unauthorized`，控制台错误与未捕获页面错误均为 `0`。
- **三大桌面视口布局核查（`http://127.0.0.1:7630/login`）**：
  - **`1440x900`**：`html` / `body` / `#root` 边界与视口严格一致（`1440x900`），滚轮横纵向位移 `dx = 0, dy = 0`，无离屏/截断元素；最终 `1440x900` 截图确认奶油白画布、左侧芦荟绿说明区与右侧纯黑胶囊主按钮符合 `DESIGN.md`。
  - **`1920x1080`**：`html` / `body` / `#root` 边界为 `1920x1080`，滚轮横纵向位移 `dx = 0, dy = 0`，无溢出与裁切。
  - **`2560x1440`**：`html` / `body` / `#root` 边界为 `2560x1440`，滚轮横纵向位移 `dx = 0, dy = 0`，无溢出与裁切。
- **后端进程状态客观说明（`http://127.0.0.1:7620/openapi.json`）**：
  - 只读读取当前 `uvicorn` 进程的 `/openapi.json` 返回 `200 OK`，当前已注册 `26` 条路径（尚为重启前的阶段 3 进程），暂未包含 `/refunds` 与 `/inventory`；本地验证阶段 4 线上联调前，需人工重启本地 `uvicorn` 后端进程与 `app.workers.consumer` 消费者进程以加载阶段 4 路由及 `commerce.jobs.refund` 队列监听。
