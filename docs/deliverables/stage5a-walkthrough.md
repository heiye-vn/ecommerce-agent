# 阶段 5A 交付验证报告：调价审批闭环、经营分析与活动复盘

阶段 5A 已按 [阶段 5 统一实施计划](./stage5-implementation-plan.md)（方案 1）完成调价审批执行闭环、调价 Agent、经营分析看板、促销活动复盘后端服务与桌面端图表工作台全部交付，并已更新 [README.md](../../README.md)；所有后端检查、迁移播种与前端测试构建均已验证通过（阶段 5B 知识库 + `pgvector` + RRF + 百炼重排将在本批验收后按计划推进）。

---

## 一、核心交付清单

### 1. 配置基线、数据模型与可回滚迁移（`0009`）
- **模型配置预埋（5B 准备）**：更新 [.env.example](../../.env.example) 与 [config.py](../../backend/app/core/config.py)，新增 `dashscope_embedding_model`（`qwen3.7-text-embedding-flash`）、`dashscope_embedding_dimensions`（`1024`）、`dashscope_rerank_model`（`qwen3.7-text-rerank`）与 `dashscope_rerank_url` 配置项。
- **调价请求模型**：新增 [price_change.py](../../backend/app/models/price_change.py)（`PriceChangeRequest`），包含 `uq_price_changes_active_sku` 部分唯一索引（约束同店铺同 SKU 同时仅允许一笔 `pending_approval / approved / processing` 在途调价）、店铺 + 申请单号唯一约束及复合外键。
- **既有模型与约束扩展**：更新 [catalog.py](../../backend/app/models/catalog.py)（`PriceHistory` 补充 `shop_id`、`actor_id`、`approval_no`、`price_change_request_id`、`reason`、`fk_price_histories_shop_sku` 及 `ix_price_histories_shop_sku_changed`）、[reliability.py](../../backend/app/models/reliability.py)（`ck_jobs_type` 新增 `price_change_execute`，`ck_notifications_category` 新增 `price_change`）与 [models/__init__.py](../../backend/app/models/__init__.py)。
- **Alembic 迁移与演练数据**：新增可回滚迁移 [0009_price_changes_and_analytics.py](../../backend/alembic/versions/0009_price_changes_and_analytics.py)（`downgrade()` 先清理阶段 5A 相关任务/Outbox/通知记录再收窄约束），以及幂等初始化脚本 [services/seed_stage5a.py](../../backend/app/services/seed_stage5a.py) 与 [scripts/seed_stage5a.py](../../backend/scripts/seed_stage5a.py)（补充 `SKU-018`、`SKU-011`、已完成与生效中促销活动、多日订单与 1 笔退款及 `SKU-001` 早期调价历史，保持 `SKU-001` 在 `2026-11-14 ~ 2026-11-21` 的 `120` 件基线销量与 `SKU-002` 库存边界不被破坏）。

### 2. 调价审批执行闭环、RabbitMQ 队列与调价 Agent
- **调价服务与接口**：新增 [schemas/price_changes.py](../../backend/app/schemas/price_changes.py)、[services/price_changes.py](../../backend/app/services/price_changes.py) 与 [api/price_changes.py](../../backend/app/api/price_changes.py)，提供列表、历史、详情、提单审批与撤回接口，保持幂等去重、成本底线保护、原价未变更拦截与执行期二次实时原价漂移校验（`409`/`422` 显式报错及失败状态落库，不误写售价与价格流水）。
- **审批与 Consumer 队列泛化**：更新 [schemas/jobs.py](../../backend/app/schemas/jobs.py)、[schemas/notifications.py](../../backend/app/schemas/notifications.py)、[services/approvals.py](../../backend/app/services/approvals.py)、[services/jobs.py](../../backend/app/services/jobs.py)、[topology.py](../../backend/app/workers/topology.py) 与 [consumer.py](../../backend/app/workers/consumer.py)，在 `TopologyConfig.production()` 增加 `commerce.jobs.price_change` 与 `commerce.jobs.price_change.dlq`，由既有 `app.workers.consumer` 通过 `asyncio.gather` 并发消费，不新增第 4 个 Worker 进程。
- **调价 Agent 升级**：新增 [services/price_change_preview.py](../../backend/app/services/price_change_preview.py)，更新 [schemas/agent.py](../../backend/app/schemas/agent.py)、[llm_gateway.py](../../backend/app/agent/llm_gateway.py)、[tools.py](../../backend/app/agent/tools.py)、[services/agent.py](../../backend/app/services/agent.py)、[graph.py](../../backend/app/agent/graph.py) 与 [api/agent.py](../../backend/app/api/agent.py)，支持单 SKU 调价缺参追问、调价方案预览（含成本线与历史调价上下文）及 `confirm_plan` 显式确认后幂等送审。

### 3. 经营分析与促销活动复盘接口
- **分析与复盘路由**：新增 [schemas/analytics.py](../../backend/app/schemas/analytics.py)、[services/analytics.py](../../backend/app/services/analytics.py) 与 [api/analytics.py](../../backend/app/api/analytics.py)，并在 [api/promotions.py](../../backend/app/api/promotions.py) 增加活动复盘端点，统一挂载至 [main.py](../../backend/app/main.py)：
  - `GET /api/shops/{shop_id}/analytics/overview`（采用选项 A，由数据库真实聚合计算客单价 `paid_gmv / paid_orders`）
  - `GET /api/shops/{shop_id}/analytics/daily-report`（严格区分数据库事实与分析推测）
  - `GET /api/shops/{shop_id}/analytics/inventory-turnover`（明确标注按件数示例口径，非财务成本周转率）
  - `GET /api/shops/{shop_id}/promotions/{promotion_id}/review`（活动期 vs 等长基线期销量/GMV 对比）

### 4. 前端桌面端与 Apache ECharts 接入
- **图表封装与路由查询**：在 [package.json](../../frontend/package.json) 引入 `echarts`（`^6.1.0`），新增异步按需加载封装 [EChartBox.tsx](../../frontend/src/components/EChartBox.tsx)（仅在真实非零容器尺寸初始化 SVG ECharts 实例，`jsdom` 零尺寸环境走 `headless` 分支且生产打包拆分为独立异步 chunk），更新 [queries.ts](../../frontend/src/api/queries.ts) 与 [router.tsx](../../frontend/src/router.tsx)。
- **工作台页面升级**：
  - 经营分析 [analytics/index.tsx](../../frontend/src/pages/analytics/index.tsx) & [analytics/index.module.css](../../frontend/src/pages/analytics/index.module.css)：落地指标看板（含客单价卡与趋势图）、完整自然日日报、库存周转与异常三 Tab；
  - 商品运营 [catalog/index.tsx](../../frontend/src/pages/catalog/index.tsx) & [catalog/index.module.css](../../frontend/src/pages/catalog/index.module.css)：新增调价台/历史 Tab 及调价审批执行/漂移失败详情路由 `/catalog/price-changes/:requestId`；
  - 促销管理 [promotions/index.tsx](../../frontend/src/pages/promotions/index.tsx) & [promotions/index.module.css](../../frontend/src/pages/promotions/index.module.css)：新增促销日历与 ECharts 活动复盘对比 Tab（并清理 Modal `destroyOnClose` 弃用告警为 `destroyOnHidden`）；
  - 审批、任务、通知与 Agent 工作台：[approvals/index.tsx](../../frontend/src/pages/approvals/index.tsx)、[tasks/index.tsx](../../frontend/src/pages/tasks/index.tsx)、[notifications/index.tsx](../../frontend/src/pages/notifications/index.tsx)、[agent/index.tsx](../../frontend/src/pages/agent/index.tsx)。

---

## 二、自动化测试与构建验证结果

| 检查项 | 执行命令 | 实测结果 |
| --- | --- | --- |
| 后端风格检查 | `cd backend && uv run ruff check app tests scripts` | **All checks passed!** |
| 后端静态类型检查 | `cd backend && uv run mypy app` | **Success: no issues found in 72 source files** |
| 后端数据库迁移与表结构测试 | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_database.py`<br>`cd backend && uv run alembic upgrade head` | **3 passed**（含 `price_change_requests` 校验）；本机 `7600` 数据库已升级至 `0009_price_changes_and_analytics` |
| 后端全量测试（含 PG + RabbitMQ 集成与 Agent 评测） | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest` | **45 passed, 17 warnings in 27.52s**（覆盖 [test_stage5a_price_analytics.py](../../backend/tests/integration/test_stage5a_price_analytics.py)、[test_stage5a_price_agent.py](../../backend/tests/agent/test_stage5a_price_agent.py) 与 [test_agent_gateway.py](../../backend/tests/unit/test_agent_gateway.py) 等；17 条 warning 来自第三方 `langsmith` 包的 `asyncio.iscoroutinefunction` 弃用提示） |
| 阶段 5A 种子脚本幂等验证 | `cd backend && uv run python -m scripts.seed_stage5a`（连跑两次） | 首次返回 `{'stage5a_skus': True, 'stage5a_orders': True, 'stage5a_promotions': True, 'stage5a_price_history': True}`；二次全部返回 `False` |
| 前端 Vitest 单元测试 | `cd frontend && npm test -- --run` | **Test Files 9 passed (9)，Tests 37 passed (37)**（Duration `13.66s`；含新增 [stage5a_price_analytics.test.tsx](../../frontend/tests/stage5a_price_analytics.test.tsx) 5 条用例，同时通过 [vitest.config.ts](../../frontend/vitest.config.ts) 与 [setup.ts](../../frontend/tests/setup.ts) 消除并发超时竞争） |
| 前端 TypeScript 与生产构建 | `cd frontend && npm run build` | **`tsc && vite build` 成功（0 错误，built in 355ms）**，拆出独立 ECharts 异步 chunk（`1,124.57 kB`，gzip `373.52 kB`） |

---

## 三、桌面端浏览器核验与进程状态提示

遵循不擅自启停用户本地后台进程的原则，复用本机运行中的服务进行只读浏览器核验（经 `BROWSER_HOST_FACTS` 确认访问源为 `http://127.0.0.1:7630` 与 `http://127.0.0.1:7620`，`blocked_origins: none`）：

1. **前端受保护路由与无运行时报错核验（`http://127.0.0.1:7630`）**：
   - 未登录访问 `http://127.0.0.1:7630/analytics`、`http://127.0.0.1:7630/catalog/price-changes/1` 与 `http://127.0.0.1:7630/promotions?tab=review`，均返回 HTTP `200` 并重定向至 `/login`（完整渲染“登录工作台”）；
   - 网络侧仅观察到预期的 `POST http://127.0.0.1:7630/api/auth/refresh -> HTTP 401 Unauthorized`，浏览器 HUD 未记录任何控制台报错或页面异常。
2. **三大桌面视口边界核验（`/login`）**：
   - **`1920x1080`**：`html` / `body` / `#root` 边界为 `1920x1080`，滚轮横纵向位移 `dx = 0, dy = 0`，溢出/裁剪元素 `0` 个；
   - **`2560x1440`**：`html` / `body` / `#root` 边界为 `2560x1440`，滚轮横纵向位移 `dx = 0, dy = 0`，溢出/裁剪元素 `0` 个；
   - **`1440x900`**：`html` / `body` / `#root` 边界为 `1440x900`，滚轮横纵向位移 `dx = 0, dy = 0`，溢出/裁剪元素 `0` 个，并已人工查验最终 `1440x900` 截图确认奶油白画布、左侧薄荷绿说明区与右侧纯黑胶囊主按钮正常渲染。
3. **后端运行进程实时状态提示（`http://127.0.0.1:7620/openapi.json`）**：
   - 本次只读访问 `http://127.0.0.1:7620/openapi.json` 返回 `32` 条路径（尚未包含 `/price-changes`、`/analytics/`、`/review`），符合预期：该后端与 Worker 进程上次重启于阶段 4，尚未加载阶段 5A 新代码。
   - **联调前需人工重启进程**：数据库迁移与种子数据已在 `7600` 库就绪，请先重启后端 `uvicorn`（`127.0.0.1:7620`）与 `app.workers.consumer`（新增监听 `commerce.jobs.price_change` 队列）再体验调价闭环、经营分析与活动复盘页面。
