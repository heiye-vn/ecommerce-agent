# 阶段 2 交付验证报告：促销、审批、幂等、并发排期与 Agent 闭环

阶段 2 已按 [stage2_implementation_plan.md](./stage2-implementation-plan.md) 全部实施并通过自动化与浏览器验收。

---

## 一、核心交付清单

### 1. 数据模型与可回滚迁移
- 新增 [promotion.py](../../backend/app/models/promotion.py) 与 [reliability.py](../../backend/app/models/reliability.py)，以及 Alembic 迁移 [0006_promotions_approvals_audit.py](../../backend/alembic/versions/0006_promotions_approvals_audit.py)，落地 5 张核心表：
  - `promotions`、`promotion_items`（复合外键绑定店铺隔离、状态枚举约束、`ends_at > starts_at` 检查、时段窗口索引）
  - `approvals`（不可变 `payload_snapshot` JSONB 快照、`(resource_type, resource_id, resource_version)` 唯一约束）
  - `operation_requests`（`(organization_id, operation_type, idempotency_key)` 唯一键与 `request_hash` 冲突检测）
  - `audit_logs`（全链路不可变审计表）

### 2. 确定性后端核心服务与 HTTP API
- **幂等与审计引擎**：[idempotency.py](../../backend/app/services/idempotency.py)（`INSERT ... ON CONFLICT DO NOTHING` + `FOR UPDATE` 原子抢占，支持同键重放与同键异参 `409 IDEMPOTENCY_CONFLICT`）、[audit.py](../../backend/app/services/audit.py)。
- **促销状态机、V2 修订与锁行并发排期**：[promotions.py](../../backend/app/services/promotions.py) 与 [api/promotions.py](../../backend/app/api/promotions.py)。
  - 显式有限状态机校验、严格重叠判定（首尾相接允许排期）、撤回与 V2 修订递增版本（旧审批单完整留档）；
  - `schedule_approved_promotion` 在事务内先 `SELECT ... FOR UPDATE` 锁定目标 SKU，再实时复核售价、库存 ≥ 100、商品在售、开始时间与重叠活动，确保并发重叠方案至多一份排期成功。
- **通用审批裁决与风控隔离**：[approvals.py](../../backend/app/services/approvals.py) 与 [api/approvals.py](../../backend/app/api/approvals.py)，硬拦截无审批角色、禁止自审（`SELF_APPROVAL_FORBIDDEN`）、禁止跨店审批（`CROSS_SHOP_APPROVAL_DENIED`）与禁止重复裁决（`APPROVAL_ALREADY_DECIDED`）。

### 3. Agent 实时冲突校验与确认送审闭环
- [promotion_preview.py](../../backend/app/services/promotion_preview.py) 接入实时数据库排期冲突检查并生成可确认方案 `plan_key`。
- [agent/tools.py](../../backend/app/agent/tools.py) 新增受控写工具 `submit_promotion_approval_tool`（绝不暴露审批通过能力），[agent/graph.py](../../backend/app/agent/graph.py) 支持自然语言“确认刚才的方案”幂等送审、重复确认返回原单与跨店拦截。
- 新增沙箱演练脚本 [seed_stage2.py](../../backend/scripts/seed_stage2.py)，为本地沙箱补充独立审批员 `approver.demo`、第二家授权店「茶饮专营店」及库存 99 件 / 下架边界 SKU。

### 4. 前端完整闭环页面（对齐 `DESIGN.md` 与 `prototypes/png/`）
- **导航与路由**：[App.tsx](../../frontend/src/App.tsx) 与 [router.tsx](../../frontend/src/router.tsx) 新增分组导航「审批中心」及 `/promotions`、`/promotions/:promotionId`、`/approvals`、`/approvals/:approvalId` 路由。
- **Agent 对话页升级**：[pages/agent/index.tsx](../../frontend/src/pages/agent/index.tsx) 支持真实排期冲突结果、点击「确认并提交审批」与对话确认送审、展示「已提交人工审批」卡片（含幂等提示与撤回）及右侧「任务进度」时间线。
- **促销管理页**：[pages/promotions/index.tsx](../../frontend/src/pages/promotions/index.tsx) 实现列表筛选/分页、新建促销草案弹窗、详情 4 格指标带、撤回申请、V2 修订重新送审及不可变审计时间线。
- **审批中心页**：[pages/approvals/index.tsx](../../frontend/src/pages/approvals/index.tsx) 实现多视角审批队列、不可变参数快照卡片、数据依据、审批权限边界提示（禁止自审/无权限/已裁决）及批准/驳回裁决反馈。

---

## 二、自动化测试与静态检查证据

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 后端风格检查 | `cd backend && uv run ruff check app tests scripts` | **All checks passed!** |
| 后端静态类型检查 | `cd backend && uv run mypy app` | **Success: no issues found in 43 source files** |
| 后端全量测试（含真实 PG 并发压测） | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest` | **34 passed**（覆盖 Alembic `upgrade/downgrade/check`、幂等重放与同键异参 `409`、自审/跨店/重裁拦截、`asyncio.gather` 行锁并发重叠排期、涨价复核失败 + V2–V4 修订、阶段 2 Agent 验收矩阵） |
| 本地开发库迁移与沙箱初始化 | `alembic upgrade head && python -m scripts.seed_stage2` | **Exit 0**（已升至 `0006_promotions_approvals_audit` 并写入审批员与边界 SKU） |
| 前端 Vitest 自动化测试 | `cd frontend && npm test -- --run` | **6 test files, 26 passed**（覆盖 Agent 确认送审、促销详情与 V2 修订、审批快照裁决及原有全量回归） |
| 前端 TypeScript 与生产构建 | `cd frontend && npm run build` | **`tsc && vite build` 成功，0 错误** |

---

## 三、浏览器接口与三视口桌面验收

- **OpenAPI 路由核验**：`http://127.0.0.1:7629/openapi.json` 返回 `200 OK`（20 paths），阶段 2 全部 6 条促销与审批核心路由均已注册。
- **就绪探针核验**：`/api/health/ready` 返回 `200 OK`，`postgresql` / `redis` / `rabbitmq` 均为 `ok`。
- **路由守卫核验**：未登录访问 `/promotions` 与 `/approvals` 均自动重定向至 `/login`。
- **桌面三视口检测**（`1440x900`、`1920x1080`、`2560x1440`）：三种视口下根容器与视口严格对齐，横/纵向滚轮测试位移均为 `0`，无溢出与控制台未捕获 JS 错误。
