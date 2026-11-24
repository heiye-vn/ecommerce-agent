# 电商运营 Agent 架构与业务整改验证报告（P0 → P1 → P2）

本次整改按批准后的 [implementation_plan.md](./stage1-hardening-plan.md) 全部实施并通过全量验证。

---

## 一、整改交付清单

### P0：核心架构与安全边界
1. **配置与数据库生命周期收口，彻底拆离长事务与外部 LLM I/O**
   - 新增 [config.py](../../backend/app/core/config.py)，在 [main.py](../../backend/app/main.py) 的 `lifespan` 中一次性加载配置并初始化单例 `AsyncEngine` 与 `async_sessionmaker`，消除请求期同步读 `.env` 磁盘与连接池懒加载竞态。
   - 改造 [api/auth.py](../../backend/app/api/auth.py) 与 [api/agent.py](../../backend/app/api/agent.py)：鉴权、历史会话加载、受控工具查数与运行时落库均采用独立短生命周期数据库会话，**LLM 推理过程完全不持有 PostgreSQL 连接与活跃事务**。
2. **统一业务错误码与细粒度角色鉴权**
   - 新增 [errors.py](../../backend/app/core/errors.py) 统一输出 `{"code", "message", "detail"}`。
   - 在 [api/auth.py](../../backend/app/api/auth.py) 中新增 `ShopContext` 与 `require_shop_roles(*roles)` 守卫；运营 Agent 接口限定 `Role.OPERATOR` / `Role.ADMIN`，纯 `approver` 调用返回 `403 SHOP_ROLE_FORBIDDEN`。
3. **Agent 单向分层重构与服务端会话持久化**
   - 确立单向调用链：`api/agent.py` → `agent/graph.py` → `agent/tools.py` → `services/*`，彻底消除 `services` 反向依赖 `agent` 的循环边界。
   - 新增 [models/agent.py](../../backend/app/models/agent.py) 与迁移 [0004_agent_runtime.py](../../backend/alembic/versions/0004_agent_runtime.py)，落地 `agent_sessions`、`agent_messages`、`agent_tasks`、`agent_tool_calls` 四张表。
   - 升级 [services/agent.py](../../backend/app/services/agent.py) 与 [agent/tools.py](../../backend/app/agent/tools.py)：由服务端 `session_id` 托管多轮上下文并执行跨店拦截；销量与促销预览统一经受控工具层执行并落库工具调用审计记录与 `llm_provider`/`model_name` 元数据。

### P1：业务规则加固、时空抽象与数据层优化
1. **统一业务时钟与店铺时区**
   - 新增 [clock.py](../../backend/app/core/clock.py)，通过 `SANDBOX_BUSINESS_DATE` 驱动固定业务日或实时时钟，解除业务服务对 `seed_catalog.DEMO_DATE` 的反向依赖。
   - `shops` 表新增 `timezone` 列（默认 `Asia/Shanghai`），[orders.py](../../backend/app/services/orders.py) 与 [promotion_preview.py](../../backend/app/services/promotion_preview.py) 按店铺自身时区计算自然日窗口。
2. **LLM 结构化槽位提取与破价风控**
   - 升级 [llm_gateway.py](../../backend/app/agent/llm_gateway.py)（缓存 `ChatOpenAI` 客户端、支持按渠道切换 `function_calling`/`json_schema`）与 `QueryIntent` 结构化槽位抽取，后端确定性校验兼容自然口语（如“打八折做三天”）同时严拒模糊区间与时钟点。
   - 在 [promotion_preview.py](../../backend/app/services/promotion_preview.py) 中增加成本价查询与 `promotion_price < cost` 负毛利风控预警（`cost_price`、`below_cost`、`risk_warnings`）。
3. **数据库状态约束与高频索引补齐**
   - 新增迁移 [0005_shop_timezone_and_indexes.py](../../backend/alembic/versions/0005_shop_timezone_and_indexes.py)：添加 `shops.timezone`、`orders` 状态枚举检查约束 `ck_orders_status`，并补充 `order_items(shop_id, sku_id)`、`order_items(shop_id, order_id)`、`inventory_movements(sku_id, created_at)`、`price_histories(sku_id, changed_at)` 索引；`sku_sales` 显式限定曾支付成功状态集合。

### P2：前端架构治理与工程质量门禁
1. **前端 API 层、TanStack Query 与多店铺 Layout Route**
   - 新增 [client.ts](../../frontend/src/api/client.ts) 与 [queries.ts](../../frontend/src/api/queries.ts)，统一处理 Bearer 鉴权、401 单飞刷新重放（修复多店下只校验 `shops[0]` 的缺陷）、`ApiError` 业务码解析及预留 `Idempotency-Key` 注入；[catalog](../../frontend/src/pages/catalog/index.tsx) 与 [orders](../../frontend/src/pages/orders/index.tsx) 迁移至 TanStack Query。
   - 重构 [router.tsx](../../frontend/src/router.tsx) 为 Layout Route，维护 `activeShopId` 并在 [App.tsx](../../frontend/src/App.tsx) 顶部栏实现符合 [DESIGN.md](../../DESIGN.md) 规范的芦荟绿胶囊多店铺切换器。
   - 更新 [agent/index.tsx](../../frontend/src/pages/agent/index.tsx) 使用服务端 `session_id`、展示破价风控预警、工具调用次数与跨店会话提示。
2. **真实依赖探活与静态门禁**
   - 在 [api/health.py](../../backend/app/api/health.py) 与 [services/health.py](../../backend/app/services/health.py) 保留 `/api/health` 并新增 `/api/health/live` 与 `/api/health/ready`（真实探测 PostgreSQL / Redis / RabbitMQ 连通性）。
   - 在 [backend/pyproject.toml](../../backend/pyproject.toml) 引入 `ruff` 与 `mypy` 质量门禁。

---

## 二、自动化验证证据

| 检查项 | 执行命令 | 结果 |
| --- | --- | --- |
| 后端代码风格检查 | `cd backend && uv run ruff check app tests scripts` | **All checks passed!** |
| 后端静态类型检查 | `cd backend && uv run mypy app` | **Success: no issues found in 32 source files** |
| 后端单测 + 真实 PG 集成测试 | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest` | **30 passed**（含 Alembic `upgrade/downgrade/check`、角色守卫、跨店会话拦截、4 张 Agent 运行时表落库、破价风控、`/api/health/ready` 真实探活） |
| 本地开发库迁移 | `cd backend && uv run alembic upgrade head` | **Exit 0**（已升至 `0005_shop_timezone_and_indexes`） |
| 前端 Vitest 自动化测试 | `cd frontend && npm test -- --run` | **5 test files, 24 passed**（含服务端 `session_id` 多轮对话、破价警告、多店铺切换与跨店拦截、TanStack Query 401 续期） |
| 前端类型与生产构建 | `cd frontend && npm run build` | **`tsc && vite build` 成功，0 错误** |

---

## 三、桌面端三视口与就绪探活浏览器验收

- **就绪探活验收**：浏览器访问 `/api/health/ready` 返回 `200 OK`：
  `{"status":"ok","service":"commerce-agent-backend","checks":{"postgresql":"ok","redis":"ok","rabbitmq":"ok"}}`
- **三视口布局检测**（无控制台未捕获 JS 错误，滚轮实测横向/纵向位移均为 `0`）：

| 视口 | 根容器尺寸 | 横向/纵向滚动条 | 视觉检查结论 |
| --- | --- | --- | --- |
| `1440x900` | `1440 x 900` | 无（`scrolledX=0, scrolledY=0`） | 奶油白画布、浅绿卡片、黑胶囊按钮对齐清晰无截断 |
| `1920x1080` | `1920 x 1080` | 无（`scrolledX=0, scrolledY=0`） | 留白均衡，无元素重叠或溢出 |
| `2560x1440` | `2560 x 1440` | 无（`scrolledX=0, scrolledY=0`） | 比例协调，无全局滚动条 |
