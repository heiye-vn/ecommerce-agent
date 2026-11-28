# 阶段 4 实施计划：退款、订单售后 Agent 与库存并发

## 一、目标、范围与关键架构决策

严格对齐 [docs/product-design.md](../../docs/product-design.md) 第 2.4、2.8、4.2、4.3、6、7、9 节，落地项目第二个完整可控执行闭环（**退款售后**）与高并发核心能力（**库存并发控制与不可变流水**）：

> 自然语言查单/异常诊断/退款诉求 → 只读资格与余额核验（缺失金额或原因时追问） → 运营显式确认并幂等送审 → 独立审批人核对退款快照并裁决 → PostgreSQL Outbox + RabbitMQ 异步执行（行锁二次复核、防重复退款与超额退款、瞬态故障退避重试与 DLQ 人工恢复） → 全程不可变审计与站内通知。

### 关键设计原则与边界
- **零新增第三方依赖**：完全复用现有 FastAPI / SQLAlchemy 2.x / `aio-pika` / LangGraph / React / Ant Design 5 / TanStack Query。
- **不增加本地常驻进程数量**：RabbitMQ 在既有 `commerce.events` / `commerce.dlx` 交换机下新增独立业务队列 `commerce.jobs.refund` + `commerce.jobs.refund.dlq`（路由键 `refund.execute.requested`），由现有 `app.workers.consumer` 同一进程并发消费促销与退款两条队列，无需重删旧队列，也无需多开第 4 个 Worker 终端。
- **明确排除范围（留待阶段 5/6）**：不涉及调价审批/调价记录、经营分析 ECharts 看板、知识库 RAG/Rerank 及管理员故障注入台。
- **严格模块化单体边界**：`api/` 与 `agent/tools.py` 一律经由 `services/` 访问数据库；Agent 工具绝不暴露任何审批通过/驳回或直接扣减库存的写能力。

---

## 二、数据库表设计与 Alembic 迁移（`0008_refunds_and_inventory_ops.py`）

新增可回滚迁移 [0008_refunds_and_inventory_ops.py](../../backend/alembic/versions/0008_refunds_and_inventory_ops.py)，覆盖三项演进并保证与现有阶段 1–3 数据和测试 100% 兼容：

| 表 | 变更内容 | 核心约束与工程意图 |
| --- | --- | --- |
| `refunds`（新增） | 新增模型 [backend/app/models/refund.py](../../backend/app/models/refund.py)：`organization_id`、`shop_id`、`order_id`、`refund_no`（店内唯一 `RFD-YYYYMMDD-NN`）、`amount`（`Numeric(12, 2)`）、`reason`、`status`、`requester_id`、`failure_reason`、`processed_at` | 1. 复合外键 `(shop_id, order_id) -> orders(shop_id, id)` 保证租户隔离；<br>2. 状态检查约束 `status IN ('pending_approval', 'approved', 'processing', 'succeeded', 'rejected', 'cancelled', 'failed')`；<br>3. `amount > 0`；<br>4. **部分唯一索引 `uq_refunds_active_order`**：`ON refunds(shop_id, order_id) WHERE status IN ('pending_approval', 'approved', 'processing')`，从数据库层兜底阻止同一订单并发产生多笔在途退款申请。 |
| `inventory_movements`（扩展） | 补充 `shop_id`（迁移中按 `skus.shop_id` 回填后设非空）、`available_delta`、`reserved_delta`、`before_available`、`after_available`、`before_reserved`、`after_reserved`、`actor_id`、`note` | 保持不可变流水只增不改；新增 `ck_inventory_movements_type` 枚举约束（`reserve` / `release` / `fulfill` / `restock` / `adjust`），清晰还原每笔操作前后快照变化，对齐原型 `08-库存与流水.png`。 |
| `orders`（轻量扩展） | 新增 `fulfillment_status`（默认 `'pending_shipment'`，检查约束 `pending_shipment / shipped / delivered / cancelled`）与可空 `exception_type`（如 `buyer_cancelled`、`inventory_review`、`logistics_sync`） | 支撑原型 `16-订单详情.png` 与 `55-异常订单筛选与建议.png` 的履约状态与异常筛选，所有历史种子订单平滑兼容。 |

---

## 三、后端确定性服务层、并发控制与异步执行

### 3.1 退款状态机与防重复退款引擎（新增 [backend/app/services/refunds.py](../../backend/app/services/refunds.py)、[backend/app/api/refunds.py](../../backend/app/api/refunds.py)）
- **显式状态机**（非法流转统一返回 `409 INVALID_REFUND_STATE`）：
  ```text
  pending_approval ─┬─→ approved ─→ processing ─┬─→ succeeded
                    ├─→ rejected                └─→ failed（业务复核失败）
                    └─→ cancelled（申请人/管理员撤回）
  ```
- **退款资格计算（`evaluate_order_refund_eligibility`）**：
  - 实时汇总该订单 `status == 'succeeded'` 的 `refunded_amount` 与 `status IN ('pending_approval', 'approved', 'processing')` 的 `in_flight_amount`；
  - 仅 `orders.status IN ('paid', 'partially_refunded')` 允许发起退款；非支付态返回 `409 ORDER_NOT_REFUNDABLE`；
  - 存在在途申请返回 `409 REFUND_IN_FLIGHT_EXISTS`；超额返回 `422 REFUND_AMOUNT_EXCEEDS_REMAINING`。
- **单事务提交审批（`submit_refund`）**：
  - 受 `operation_requests` 幂等键保护（同键同参重放原审批单，同键异参返回 `409 IDEMPOTENCY_CONFLICT`）；
  - 事务内先 `SELECT ... FROM orders WHERE shop_id = :shop_id AND id = :order_id FOR UPDATE` 锁定订单行，复核资格与可退余额，写入 `refunds` + `approvals`（不可变快照含订单号、实付、当时已退、申请退款额、原因）+ `audit_logs` + 审批员 `notifications`。
- **撤回退款（`cancel_refund`）**：仅原申请人或管理员可撤回 `pending_approval` 退款单，同步取消关联审批单并写审计。
- **异步执行退款（`execute_approved_refund`）**：
  - 供 `jobs.py` 在 Consumer 事务内调用：先 `FOR UPDATE` 锁定 `orders` 行与 `refunds` 行，**二次实时复核订单状态与剩余可退余额**（绝不盲信审批快照）；
  - 若业务条件已失效（如订单已被其他途径退完或金额超限），置为 `failed` 并记录 `failure_reason`（业务失败不可直接重试）；
  - 若校验通过，经 `processing` → 模拟本地沙箱执行成功置为 `succeeded` 并写入 `processed_at`，同时原子更新 `orders.status` 为 `partially_refunded` 或 `refunded`，写入 `audit_logs` 与运营 `notifications`。

### 3.2 审批中心、异步任务与 Worker 泛化
- **[backend/app/services/approvals.py](../../backend/app/services/approvals.py)**：
  - 取消 `resource_type != "promotion"` 硬限制，完整支持 `resource_type in ("promotion", "refund")`；
  - 列表与详情按资源类型解析 `resource_no`（`RFD-...`）、`resource_name`（订单号+原因摘要）、`snapshot_amount` 与 `Refund.status / failure_reason`；
  - 批准退款后沿用 `ASYNC_EXECUTION_MODE`：写入 `job_type="refund_execute"` 任务与 Outbox 事件（`outbox` 模式返回 `queued`，`inline` 模式同步执行）；驳回时同步置 `refund.status = "rejected"` 并通知申请人。
  - 原有风控守卫（禁止自审、跨店拒绝、无 `approver` 角色拒绝、重复裁决拒绝）对退款审批天然生效。
- **[backend/app/services/jobs.py](../../backend/app/services/jobs.py) & [backend/app/schemas/jobs.py](../../backend/app/schemas/jobs.py)**：
  - 扩展 `JobType` 支持 `"refund_execute"`，映射事件 `"refund.execute.requested"`；
  - 在 `list_jobs` / `get_job_detail` 中为 `Promotion` 的 `outerjoin` 增加 `Job.resource_type == "promotion"` 条件，并补充 `Refund` 的 `outerjoin`（条件 `Job.resource_type == "refund"`），彻底杜绝跨资源类型 ID 碰撞串数据；
  - 复用 `TransientJobError` 指数退避重试、DLQ 死信与 `retry_dead_letter_job` 人工重试，业务失败 `failed` 继续禁止直接重试。
- **[backend/app/workers/topology.py](../../backend/app/workers/topology.py) & [backend/app/workers/consumer.py](../../backend/app/workers/consumer.py)**：
  - 在既有 `commerce.jobs.promotion` 队列之外，声明并绑定 `commerce.jobs.refund`（routing key `refund.execute.requested`）与 `commerce.jobs.refund.dlq`；
  - `run_consumer` 同时消费两条队列，沿用“事务提交后手动 ACK、消费幂等跳过终态”的可靠语义。

### 3.3 库存并发控制与不可变流水（新增 [backend/app/services/inventory.py](../../backend/app/services/inventory.py)、[backend/app/api/inventory.py](../../backend/app/api/inventory.py)）
- **核心接口**：
  - `GET /api/shops/{shop_id}/inventory`（店铺库存概览 + 低库存预警统计，默认预警阈值 `< 30`）
  - `GET /api/shops/{shop_id}/inventory/movements`（按变动类型筛选不可变库存流水）
  - `POST /api/shops/{shop_id}/inventory/{sku_code}/movements`（受 `OperatorShop` 鉴权与 `Idempotency-Key` 保护，支持 `reserve` / `release` / `fulfill` / `restock` 操作及可选 `expected_version`）
- **并发与一致性保障**：
  1. 事务内通过 `SELECT inventory.* FROM inventory JOIN skus ... WHERE skus.shop_id = :shop_id AND skus.sku_code = :sku_code FOR UPDATE` 行级锁定库存记录；
  2. 若传入 `expected_version` 且与库内 `inventory.version` 不等，拒绝并返回 `409 INVENTORY_VERSION_CONFLICT`（乐观锁防覆盖）；
  3. 严格校验非负余额（`reserve` 要求 `available_qty >= qty`；`release` / `fulfill` 要求 `reserved_qty >= qty`），不足时返回 `409 INSUFFICIENT_INVENTORY` 或 `409 INSUFFICIENT_RESERVED_INVENTORY`；
  4. 原子更新快照、`version += 1`、追加不可变 `inventory_movements` 并记录 `audit_logs`；
  5. 遵循产品约束：异步“通知仓库”不修改库存，唯有实际落库的库存事务才产生流水。

### 3.4 订单售后 Agent 升级（`agent/llm_gateway.py`、`agent/tools.py`、`agent/graph.py`、`services/agent.py`）
- **新增订单号识别正则** `ORDER_NO_PATTERN`（匹配 `ORD-...` / `DEMO-...`）与两类新意图：
  - `order_query`：查询单笔订单详情/售后资格，或筛选本店异常订单并输出只读处理建议（不修改库存或履约状态，对齐原型 `55`）；
  - `refund`：预览退款方案（提取 `order_no`、`refund_amount`、`refund_reason`），若缺少金额或原因则进入 `refund_clarify` 多轮追问（无数据库写入）；参数齐备且资格通过后生成 `PendingRefundPlan` 并落入 `agent_sessions.context_json["pending_refund_plan"]`；
  - `confirm_plan`：根据当前会话上下文路由至促销送审或退款送审，复用 `submit_refund_approval_tool`（幂等键 `agent-session-{session_id}-refund-{plan_key}`）；重复确认幂等返回原审批单；换店确认严格跨店拦截。
- **短事务与 LLM 隔离**：完全延续阶段 1 收口架构，LLM 推理期间不持有 PostgreSQL 连接与活跃事务，所有只读与受控写工具均自动写入 `agent_tool_calls`。

### 3.5 本地演练数据脚本（新增 [backend/scripts/seed_stage4.py](../../backend/scripts/seed_stage4.py)）
- 保持 `seed.py`、`seed_catalog.py`、`seed_stage2.py` 契约不动；
- `seed_stage4.py`（仅限本地 7600 库、可重复执行幂等）补充：
  - 为沙箱 SKU 写入典型库存流水与 1 个低库存预警 SKU（`SKU-005` 硅胶杯盖，可售 18 件）；
  - 补充典型异常订单（待发货需库存复核、已取消不可退订单）及可演示部分退款的订单边界。

---

## 四、前端桌面端页面升级（对齐 `DESIGN.md` 与 `prototypes/png/`）

严格保持奶油白画布（`#FBFBF5`）、纯白卡片、细线边框、纯黑胶囊主按钮、芦荟绿高亮及 `tabular-nums`，**不新增一级侧边导航**，在现有模块内平滑扩展：

1. **商品运营（[frontend/src/pages/catalog/index.tsx](../../frontend/src/pages/catalog/index.tsx)，对齐原型 `08`）**：
   - 顶部增加「商品概览 / 库存与流水」Tab；
   - 「库存与流水」视图包含：4 格快照带（芦荟绿可售库存与版本号 `V{n}`、预占库存、库存合计、低库存预警数）、SKU 切换器、变动类型筛选、不可变流水表、右侧库存预警与“通知仓库≠预占”说明卡，以及「库存操作」弹窗（演练预占/释放/入库/履约，自动携带当前 `version` 与 `Idempotency-Key` 并友好提示版本冲突或库存不足）。
2. **订单售后（[frontend/src/pages/orders/index.tsx](../../frontend/src/pages/orders/index.tsx)，对齐原型 `15`–`19`、`55`）**：
   - 顶部增加「订单列表 / 异常订单 / 退款记录」三 Tab；
   - 「异常订单」视图支持按异常类型筛选，点击行在右侧展示异常原因与只读处理建议（可一键跳转对应订单或库存流水）；
   - 「订单详情（`/orders/:orderId`）」升级为双栏布局：左侧订单信息+商品明细+订单事件，右侧「退款资格校验」（实付/已退/在途/可退余额与重复申请提醒）、「申请退款」按钮与弹窗（携带 `Idempotency-Key`）及「关联退款」列表；
   - 新增「退款记录」Tab 与「退款详情（`/orders/refunds/:refundId`）」页面：4 格指标带（芦荟绿申请退款额、订单实付、已成功退款、本地沙箱渠道）、资格与审批摘要、右侧处理进度时间线，支持原申请人撤回待审批退款及跳转审批中心/任务中心。
3. **审批中心（[frontend/src/pages/approvals/index.tsx](../../frontend/src/pages/approvals/index.tsx)，对齐原型 `56`）与任务中心（[frontend/src/pages/tasks/index.tsx](../../frontend/src/pages/tasks/index.tsx)）**：
   - 审批详情动态识别 `resource_type === "refund"`，渲染「审阅退款申请」不可变参数快照卡片（关联订单、订单实付、当时已退、申请退款额、退款原因）与批准/驳回操作；
   - 任务中心补充 `refund_execute`（退款执行）标签、关联退款单链接与死信人工重试闭环，通知中心自动支持跳转退款详情。
4. **Agent 对话（[frontend/src/pages/agent/index.tsx](../../frontend/src/pages/agent/index.tsx)，对齐原型 `40`）**：
   - 新增快捷提示、渲染「订单退款方案」卡片（订单实付/可退上限/拟退款额/退款原因/执行约束 + 黑色胶囊「确认并提交退款审批」）、「已提交退款审批」卡片（含审批单号、退款单号、幂等提示与撤回）以及订单/异常诊断卡片。

---

## 五、分步实施顺序与验证计划

### 实施顺序（小步快跑、逐层验证）
1. **步骤 1（数据层）**：新增 `Refund` 模型、扩展 `InventoryMovement` / `Order` 模型、编写可回滚迁移 `0008` 并验证 `alembic upgrade / downgrade / check`。
2. **步骤 2（退款与审批异步闭环）**：实现 `services/refunds.py`、`api/refunds.py`，泛化 `approvals.py`、`jobs.py`、`workers/topology.py` 与 `workers/consumer.py`。
3. **步骤 3（库存并发服务）**：实现 `services/inventory.py`、`api/inventory.py` 与 `scripts/seed_stage4.py`。
4. **步骤 4（订单售后 Agent）**：扩展 `schemas/agent.py`、`llm_gateway.py`、`tools.py`、`graph.py`、`services/agent.py`。
5. **步骤 5（后端全量验证）**：新增 `tests/integration/test_stage4_refunds_inventory.py` 与 `tests/agent/test_stage4_refund_agent.py`，跑通 `ruff`、`mypy` 与全量 `pytest`（真实 PostgreSQL + RabbitMQ）。
6. **步骤 6（前端页面与 Vitest）**：完成商品库存流水、订单/异常订单/退款管理、审批/任务适配与 Agent 退款卡片，新增 `frontend/tests/stage4_refunds_inventory.test.tsx`，跑通 `vitest` 与 `vite build`。
7. **步骤 7（桌面端三视口验收与文档收尾）**：复用老大本地运行环境验证，按 `1440x900`、`1920x1080`、`2560x1440` 三视口验收，更新 `README.md` 并归档阶段 4 验证报告到 `docs/deliverables/stage4-walkthrough.md`。

### 关键后端测试验收矩阵
- **重复退款与并发退款拦截**：同一订单并发提交两笔退款申请（不同幂等键），断言**严格 1 笔成功创建、1 笔被拒绝**（`409 REFUND_IN_FLIGHT_EXISTS`）；相同 `Idempotency-Key` 重放返回原退款/审批单；同键异参返回 `409`。
- **退款状态机与二次复核**：非法状态流转拒绝 `409`；超额/未支付订单拒绝；批准后在执行前模拟余额已被耗尽，消费执行时复核置为 `failed` 且不可直接重试。
- **退款 RabbitMQ 可靠异步与死信恢复**：走完整 `Outbox → RabbitMQ (commerce.jobs.refund) → Consumer`，验证提交后 ACK 前崩溃重投幂等不重复退款、3 次瞬态失败入 DLQ 后人工重试最终成功并正确更新订单状态（`partially_refunded` → `refunded`）。
- **库存高并发压测与乐观锁**：
  - `asyncio.gather` 并发预占同一 SKU（库存 10 件、5 个并发各预占 3 件）→ 严格 3 次成功、2 次 `409 INSUFFICIENT_INVENTORY`，最终 `available_qty=1, reserved_qty=9, version=+3`，不可变流水恰好 3 条；
  - 过期 `expected_version` 请求返回 `409 INVENTORY_VERSION_CONFLICT`；预占→释放→履约全链路流水准确且幂等键重放不重复扣减。
- **售后 Agent 对照验收**：查单与异常诊断无写入；缺金额/原因追问无写入；显式确认幂等建退款审批单；重复确认返回原单；换店确认跨店拦截；要求 Agent 直接批准退款被拒绝。
