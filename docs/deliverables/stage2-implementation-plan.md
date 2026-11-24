# 阶段 2 实施计划：促销、审批、幂等、并发排期与 Agent 闭环

## 目标与验证基线

严格对照 [product-design.md](../../docs/product-design.md) 第 2.7、4.3、4.5、6、7、9 节以及 [prototypes/README.md](../../prototypes/README.md) 的首期主流程原型（`02→04`、`10`、`12`、`22`、`23`、`41–51`），打通项目首个完整的端到端业务闭环：

> 自然语言查数与追问 → 含实时冲突/破价校验的结构化方案 → 运营确认并幂等送审 → 独立审批人核对不可变快照并裁决 → 行锁实时复核并排期（支持并发冲突拦截与 V2 修订再审） → 全程不可变审计与时间线追溯。

本次阶段 2 **不引入任何新增第三方前后端依赖**，完全复用现有 FastAPI / SQLAlchemy 2.x / LangGraph / React / Ant Design 5 / TanStack Query 技术栈。

---

## 一、数据库表设计与 Alembic 迁移

### 新增模型与迁移 `0006_promotions_approvals_audit.py`
新增 [backend/app/models/promotion.py](../../backend/app/models/promotion.py) 与 [backend/app/models/reliability.py](../../backend/app/models/reliability.py)，落地 5 张核心表：

| 表名 | 核心字段与强约束 | 职责 |
| --- | --- | --- |
| `promotions` | `shop_id`, `promotion_no`(店内唯一), `name`, `version`(>=1), `status`(枚举检查), `starts_at`, `ends_at`(`ends_at > starts_at`), `minimum_qty`, `created_by`, `failure_reason`, `conflict_promotion_no`；复合唯一 `(shop_id, id)` 与窗口索引 `(shop_id, status, starts_at, ends_at)` | 促销活动主记录与版本演进 |
| `promotion_items` | `shop_id`, `promotion_id`, `sku_id`, `original_price`, `cost_price`, `discount_rate`(`0 < rate < 1`), `promotion_price`；复合外键到 `promotions(shop_id, id)` 与 `skus(shop_id, id)`；索引 `(shop_id, sku_id)` | 活动商品与核定价格依据 |
| `approvals` | `organization_id`, `shop_id`, `approval_no`(店内唯一), `action_type`, `resource_type`, `resource_id`, `resource_version`, `payload_snapshot`(JSONB), `status`(`pending/approved/rejected/cancelled`), `requester_id`, `approver_id`, `decision_comment`, `decided_at`；唯一约束 `(resource_type, resource_id, resource_version)` | 通用高风险操作不可变审批快照 |
| `operation_requests` | `organization_id`, `shop_id`, `operation_type`, `idempotency_key`, `request_hash`(SHA-256), `status`, `resource_type`, `resource_id`, `response_json`；唯一约束 `(organization_id, operation_type, idempotency_key)` | 全局业务操作幂等与同键异参冲突检测 |
| `audit_logs` | `organization_id`, `shop_id`, `actor_id`, `action`, `resource_type`, `resource_id`, `before_json`, `after_json`, `metadata_json`；复合索引 `(shop_id, resource_type, resource_id, created_at)` | 不可变操作审计流水 |

---

## 二、后端确定性服务层与并发安全

严格遵守模块化单体边界：`api/` 与 `agent/tools.py` 只调用 `services/`，且 `tools.py` **绝不暴露任何审批通过/驳回工具**。

### 2.1 幂等与审计基础设施
- **新增** [backend/app/services/idempotency.py](../../backend/app/services/idempotency.py)
  - 规范化请求体计算 `request_hash`；事务内用 PostgreSQL `ON CONFLICT DO NOTHING` + `FOR UPDATE` 原子抢占幂等记录。
  - 同键同参且已成功 → 直接返回原响应（重复提交只产生一张审批单）；同键异参 → 返回 `409 IDEMPOTENCY_CONFLICT`。
- **新增** [backend/app/services/audit.py](../../backend/app/services/audit.py)
  - 统一记录促销提交、撤回、V2 修订、审批批准/驳回、排期成功/复核失败全链路不可变审计记录，并提供按资源查询时间线接口。

### 2.2 促销状态机、冲突校验、V2 修订与锁行并发排期
- **新增** [backend/app/services/promotions.py](../../backend/app/services/promotions.py)
  - **显式有限状态机**：
    - `pending_approval` → `approved` / `rejected` / `cancelled`
    - `approved` → `scheduled` / `failed`
    - `rejected` / `cancelled` / `failed` → 修订递增 `version += 1` 重回 `pending_approval`
    - 非法状态跳转服务端统一拒返回 `409 INVALID_PROMOTION_STATE`。
  - **时段重叠判定**：同店同 SKU 状态为 `scheduled` 或 `active` 的活动，判定 `existing.starts_at < ends_at AND existing.ends_at > starts_at`（严格不等，支持首尾相接排期）。
  - **单事务创建并送审（`submit_promotion`）**：受 `Idempotency-Key` 保护，重新查库校验 SKU/商品状态、库存 ≥ 100、开始时间晚于业务日、无排期冲突，同一事务落库 `promotions` + `promotion_items` + `approvals`（含完整数据依据与破价预警快照）+ `audit_logs`。
  - **撤回（`cancel_promotion`）**：仅原申请人或管理员可撤回 `pending_approval` 活动，同步将关联待审批单置为 `cancelled` 并记审计。
  - **V2 修订重提（`revise_promotion`）**：对 `rejected` / `cancelled` / `failed` 活动递增 `version`、重算活动价与时段校验，生成新的 `approvals` 记录（旧审批单原样保留）。
  - **批准后实时复核与行锁排期（`schedule_approved_promotion`）**：
    - 校验 `promotion.version == expected_version`；
    - **并发控制核心**：在事务内执行 `SELECT id FROM skus WHERE shop_id = :shop_id AND id = :sku_id FOR UPDATE` 锁定目标 SKU；
    - 实时复核当前售价未变、实时可售库存 ≥ 100、商品/SKU 仍在售、开始时间未过、同 SKU 无时段重叠活动；任一项不满足则状态置为 `failed` 并写入具体原因与冲突活动编号，全部通过则置为 `scheduled`。

### 2.3 通用审批服务
- **新增** [backend/app/services/approvals.py](../../backend/app/services/approvals.py)
  - 队列查询支持按视角筛选：`pending_for_me`（待我审批，自动排除本人发起）、`processed_by_me`（我已处理）、`requested_by_me`（我发起的）。
  - 审批裁决硬性守卫（行锁锁定审批单）：
    - 必须拥有当前店铺 `Role.APPROVER`（`403 SHOP_ROLE_FORBIDDEN`）；
    - 跨店审批拒绝（`403 CROSS_SHOP_APPROVAL_DENIED`）；
    - **禁止自审**（`approval.requester_id == actor_id` → `403 SELF_APPROVAL_FORBIDDEN`）；
    - **禁止重复裁决**（`approval.status != 'pending'` → `409 APPROVAL_ALREADY_DECIDED`）。
  - 批准后在同一闭环内触发 `schedule_approved_promotion` 完成业务复核与排期，返回裁决与排期结果；驳回时要求填写原因并同步将活动置为 `rejected`。

### 2.4 HTTP API 路由
- **新增** [backend/app/api/promotions.py](../../backend/app/api/promotions.py) 与 [backend/app/api/approvals.py](../../backend/app/api/approvals.py)
  - `GET /api/shops/{shop_id}/promotions`、`POST /api/shops/{shop_id}/promotions`（支持 `Idempotency-Key`）
  - `GET /api/shops/{shop_id}/promotions/{promotion_id}`（含审批历史与审计时间线）
  - `POST /api/shops/{shop_id}/promotions/{promotion_id}/cancel`
  - `POST /api/shops/{shop_id}/promotions/{promotion_id}/revise`（支持 `Idempotency-Key`）
  - `GET /api/shops/{shop_id}/approvals`、`GET /api/shops/{shop_id}/approvals/{approval_id}`
  - `POST /api/shops/{shop_id}/approvals/{approval_id}/approve`、`POST /api/shops/{shop_id}/approvals/{approval_id}/reject`

---

## 三、Agent 确认送审闭环升级

1. **实时排期冲突校验接入预览**（[promotion_preview.py](../../backend/app/services/promotion_preview.py)）：
   - `PromotionPreview.conflict_check` 升级为 `"clear" | "conflict"`，新增 `conflicting_promotions: list[str]`；发现重叠已排期/生效活动时返回 `kind="promotion_blocked"` 并列明冲突活动编号。
2. **服务端托管待确认方案与受控送审工具**：
   - 完整预览生成后，服务端将待确认方案（含确定性 `plan_key`）写入 `agent_sessions.context_json["pending_plan"]`。
   - [agent/tools.py](../../backend/app/agent/tools.py) 新增受控写工具 `submit_promotion_approval_tool`（只调用 `services.promotions.submit_promotion`，使用幂等键 `agent-session-{session_id}-plan-{plan_key}`，记录到 `agent_tool_calls`）。
   - [agent/graph.py](../../backend/app/agent/graph.py) 识别“确认刚才的方案 / 确认提交”（支持 LLM `confirm_plan` 意图 + 确定性确认语兜底）：
     - 首次确认 → 返回 `kind="promotion_submitted"` 及 `approval_no`、`promotion_no`、`version`；
     - 重复确认 → 幂等命中返回同一张审批单号；
     - 换店后携带原 `session_id` 确认 → 服务端跨店拦截提示切回原店，两店均无新增审批单。

---

## 四、沙箱演练数据扩展

- 保持现有 `scripts/seed.py` 与 `scripts/seed_catalog.py` 契约完全不变（确保已有集成测试零破坏）。
- 新增 `backend/app/services/seed_stage2.py` 与 `backend/scripts/seed_stage2.py`（可重复执行、仅限本地 7600 沙箱库），补齐本地演示闭环所需数据：
  - 审批员账号 `approver.demo`（同组织「轻食旗舰店」`approver` 角色，密码写入 `.env.demo-login`）；
  - 第二家授权店铺「茶饮专营店」（为 `ops.demo` 分配 `operator` 角色，便于浏览器演示切店与跨店拦截）；
  - 边界测试 SKU：`SKU-002`（可售库存 99 件，验证库存门槛边界）、`SKU-003`（已下架状态，验证商品状态拦截）。

---

## 五、前端设计方向与页面实现

### 设计方向（遵循 `DESIGN.md` 与 `frontend-design-direction`）
- **定位与调性**：桌面端运营协同工作台，延续奶油白画布（`#FBFBF5`）、纯白内容卡片（`#FFFFFF`）、极细边框（`#E4E4E7`）、纯黑胶囊主操作（`#000000`）与芦荟/开心果绿状态与高亮表面（`#C1FBD4` / `#D4F9E0`），信息密度克制、金额与日期严格使用等宽数字（`tabular-nums`）。
- **目标视口**：仅面向桌面端 `1440x900`、`1920x1080`、`2560x1440`，无全局横向或纵向溢出滚动条。
- **核心页面与状态覆盖**（每个页面完整处理 Loading / Empty / Error / Permission / Success 状态）：
  1. **左侧工作台导航**（[App.tsx](../../frontend/src/App.tsx)）：在「业务」组保留「促销管理」，新增「系统管理 → 审批中心（`/approvals`）」入口，支持显示待处理数量胶囊。
  2. **Agent 对话页升级**（[pages/agent/index.tsx](../../frontend/src/pages/agent/index.tsx)，对齐原型 `04`）：
     - 方案预览卡片展示冲突校验结论、成本/破价警告与黑色胶囊按钮「确认并提交审批」；
     - 提交后展示「已提交人工审批」卡片（审批单号、活动编号、V1 版本、幂等说明、撤回申请按钮与跳转审批/活动详情链接）以及右侧紧凑的「任务进度」时间线。
  3. **促销管理页**（`frontend/src/pages/promotions/`，对齐原型 `10`、`12`、`47`）：
     - 列表页（`/promotions`）：按名称/编号/SKU 搜索、状态筛选、分页、「新建促销草案」弹窗；
     - 详情页（`/promotions/:promotionId`）：4 格核心数据带（芦荟绿活动价卡 + SKU + 活动开始/结束时段）、规则与审批快照卡、右侧审计时间线；支持「撤回申请」与对驳回/撤回/复核失败活动进行 **V2 修订并重新送审**。
  4. **审批中心页**（`frontend/src/pages/approvals/`，对齐原型 `22`、`23`、`49–51`）：
     - 队列页（`/approvals`）：「待我审批 / 我已处理 / 我发起的」三视图切换，主按钮「审阅快照」；
     - 审批快照与裁决页（`/approvals/:approvalId`）：不可变参数快照卡片、右侧数据依据与「审批与执行边界」状态卡（自动识别禁止自审/无审批权限/已裁决并禁用操作按钮），审批员填写意见后执行「批准此版本」（实时反馈排期成功或复核失败原因）或「驳回并返回」。

---

## 六、验证计划

1. **后端自动化验证（真实 PostgreSQL Testcontainers + 单元测试）**：
   - `alembic upgrade / downgrade / check` 全通过；
   - **幂等验证**：同一 `Idempotency-Key` 重复提交返回同一审批单号，同键异参返回 `409`；
   - **审批风控验证**：自审返回 `403`、无 `approver` 角色返回 `403`、跨店返回 `403`、重复裁决返回 `409`；
   - **并发冲突压测**：`asyncio.gather` 并发执行两份独立获批且时段重叠的促销排期，断言**严格一份 `scheduled`、一份 `failed` 且记录冲突活动编号**，以及首尾相接活动双双排期成功；
   - **复核失败与 V2 修订**：批准后模拟改价/减库存至 99 件触发复核失败 → 修订 V2 再审 → 批准排期成功，旧审批单完整保留，审计日志完整连续；
   - **阶段 2 Agent 对照验收**：查数无副作用、追问无写入、自然语言确认送审、重复确认返回原单、换店确认拦截且双店无新增审批单；
   - `uv run ruff check app tests scripts` 与 `uv run mypy app` 零告警零错误。
2. **前端自动化验证**：
   - `npm test -- --run`（覆盖 Agent 确认送审、促销列表/详情/撤回/V2 修订、审批队列/快照裁决与自审拦截）与 `npm run build` 零错误。
3. **桌面端三视口浏览器验收**：
   - 启动本地前后端并用浏览器子代理实际登录演练，在 `1440x900`、`1920x1080`、`2560x1440` 下检查 Agent 对话、促销管理、审批中心渲染、对比原型与无意外滚动条，最终交付 `walkthrough.md` 验证报告。
