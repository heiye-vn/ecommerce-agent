# 电商运营 Agent：产品、架构与核心表设计

## 1. 项目背景与定位

### 1.1 项目目标

本项目提升后端工程与 AI Agent 应用开发能力。目标不是实现完整的电商平台，也不是直接对接真实电商店铺，而是完成一个可演示、可验证的业务闭环：

> 自然语言指令 → 业务数据查询 → 执行方案生成 → 风险与权限校验 → 人工审批 → 业务执行 → 失败重试与补偿 → 全程审计。

### 1.2 产品定位

面向电商平台内部运营人员的可控执行型 Agent。运营人员通过自然语言提出业务目标，Agent 查询数据、分析并生成结构化执行方案；业务规则、权限和最终执行由确定性的后端代码负责。

### 1.3 核心设计原则

- LLM 负责意图理解、数据分析和方案建议，不直接修改数据库。
- Agent 只能通过权限受控的业务工具调用后端 API。
- 高风险操作必须经过审批，执行时重新校验业务规则。
- 商品、库存、销量和价格等事实数据必须来自数据库，不由模型生成。
- 优先做深关键闭环，不追求功能数量或完全自治。

## 2. 完整功能蓝图与业务边界

本项目采用“完整蓝图设计、分阶段实现”的方式。完整蓝图用于统一产品、前端、后端与数据设计，不要求所有细节在编码前一次定死，也不意味着所有功能同时开发。

### 2.1 运营 Agent 工作台

- 多轮对话、任务上下文和店铺切换。
- 自然语言查询商品、销量、库存、订单和促销。
- 生成促销、调价和退款等结构化执行方案。
- 展示数据依据、风险提示、缺失信息和执行进度。
- 用户确认、审批提交、取消、重试及人工处理。
- 运营规则知识库问答与引用。

### 2.2 商品与库存运营

- 商品、SPU、SKU 和店铺数据管理。
- 商品状态、当前价格、历史价格与调价审批。
- 库存快照、库存流水、预占、释放和库存预警。
- 补货建议与商品资料检查。
- 调价执行与失败处理。

### 2.3 促销运营

- 促销草案、规则校验、冲突检查和库存条件。
- 折扣、活动时间、适用 SKU 和价格依据。
- 审批、排期、生效、结束和取消。
- 促销效果查询与活动复盘。
- 重复提交防护、执行重试和审计。

### 2.4 订单与售后

- 订单查询、订单明细和履约状态。
- 异常订单筛选与处理建议。
- 退款资格、金额和重复退款校验。
- 退款审批、执行、失败处理与进度查询。
- 售后规则知识库。

退款由本地业务沙箱模拟，不接真实支付渠道。

### 2.5 经营分析

- 店铺销售、转化和退款等核心指标。
- 商品销量、库存周转与异常指标分析。
- 自然语言查数并展示查询口径。
- 日报、活动复盘和行动建议。
- 明确区分数据事实与模型推测。

### 2.6 系统管理

- 组织、用户、店铺、固定角色和数据权限。
- 审批中心、任务中心和通知中心。
- Agent 工具调用、模型调用和系统可观测性。
- 审计日志、测试数据初始化和故障模拟。

测试数据初始化与故障模拟归入沙箱管理，仅在本地业务沙箱开放，且仅组织管理员可操作。运营和审批员可在授权店铺内参与演练，但不能初始化数据或注入故障。前端按环境和角色显示入口，后端独立校验环境与管理员权限，不以菜单可见性代替鉴权。

### 2.7 首个端到端业务切片：促销运营

> 帮我看看 SKU-001 最近 7 天销量，如果库存充足，就创建一个 8 折、持续 3 天的促销。

以店铺时区 `Asia/Shanghai`、沙箱日期 2026-11-21 为例。运营补充“11 月 22 日开始”后，方案卡片应能复算出以下结果。

| 项目 | 首版规则与样例 |
| --- | --- |
| 最近 7 天销量 | 取查询日前 7 个完整自然日。本例统计 11 月 14 日 00:00（含）到 21 日 00:00（不含）之间支付成功的本店订单，按支付时间筛选，汇总 SKU 明细数量。采用支付件数口径，后续退款不抵扣。样例合计 120 件。 |
| 库存充足 | 首版沙箱固定门槛为可售库存至少 100 件，读取 `inventory.available_qty`。样例为 300 件。边界测试中 100 件通过，99 件提示库存不足。 |
| 促销价 | 后端取本店 SKU 当前售价，以十进制金额乘折扣并四舍五入到分。本例 ¥199.00 × 0.80 = ¥159.20。历史价格供运营参考。 |
| 活动时段 | 按店铺当地日历计算，含开始、不含结束。本例从 11 月 22 日 00:00 到 25 日 00:00，覆盖 22、23、24 日。缺少开始日期时追问。 |

1. Agent 在当前店铺权限范围内查询 SKU 状态、销量、库存、价格和活动冲突。条件满足且参数齐全时展示方案、数据依据和适用时段。
2. 运营确认当前版本后，后端在一个事务内保存促销草案、标记为待审批并创建关联审批单。重复确认同一版本返回原审批单。
3. 本店有审批权限且与申请人不同的人员审核快照。批准后由业务服务按第 4.3 节复核并排期，任务时间线展示同一活动的编号、执行结果和审计记录。

### 2.8 后续业务切片：退款

- 查询订单、支付金额和已有退款。
- 校验退款资格、金额和操作权限。
- 创建退款审批单。
- 按状态机执行退款，防止重复退款。
- 记录失败、重试和审计信息。

### 2.9 运营知识问答

复用已有知识库 Agent 经验，回答促销规则和操作规范，并提供引用来源。知识问答是辅助能力，不替代业务数据库查询。采用混合检索、RRF 合并和 Rerank 精排，提高文档召回与上下文相关性。

### 2.10 本项目不包含的范围

- 真实电商平台、支付和物流对接。
- 完整商城、购物车、履约和仓储系统。
- 自动定价、智能选品、销量预测和自动投放。
- 复杂动态 RBAC、多 Agent 自主协作和微服务拆分。
- 完整前端商城与消费者端购物体验。

## 3. 业务沙箱方案

项目不依赖真实电商业务系统，但后端不是页面 Mock，而是一个可读写、有业务约束和状态变化的精简电商沙箱：

```text
运营人员
   ↓ 自然语言
Agent 应用
   ↓ 受权限控制的工具调用
FastAPI 业务 API
   ↓
商品 / SKU / 库存 / 订单 / 促销 / 退款 / 审批 / 审计
   ↓
PostgreSQL
```

使用固定测试数据和初始化脚本构造典型与边界场景，例如正常商品、库存不足、商品下架、促销冲突和重复退款。数据初始化后应可重复验证。

业务接口可以抽象为统一的 `CommerceAdapter`，第一版使用本地沙箱实现，未来再替换为真实平台实现；当前不需要实现真实平台适配器。

## 4. 核心表设计

以下字段描述核心职责，不代表最终完整 DDL。所有表按需包含主键、租户归属和时间戳；业务表的枚举值、精度、唯一约束和索引在数据库设计阶段确定。

### 4.1 组织与权限

| 表 | 职责 | 关键字段或关系 |
| --- | --- | --- |
| `organizations` | 商家及租户隔离 | `id、name、status` |
| `users` | 系统用户 | `id、name、status` |
| `memberships` | 用户与组织关系 | `user_id、organization_id、role` |
| `shops` | 组织下的店铺 | `organization_id、name、platform、status` |

第一版使用固定角色枚举，例如管理员、运营和审批员；需要动态权限时再引入 `roles` 等完整 RBAC 表。店铺级权限可以通过 `memberships.shop_id` 表达：为空表示组织级权限，有值表示指定店铺权限。

### 4.2 商品、库存与订单

| 表 | 职责 | 关键字段或关系 |
| --- | --- | --- |
| `products` | SPU 商品主体 | `shop_id、name、category、status` |
| `skus` | 具体销售规格 | `product_id、sku_code、cost、current_price、status` |
| `inventory` | SKU 当前库存快照 | `sku_id、available_qty、reserved_qty、version` |
| `inventory_movements` | 不可变库存流水 | `sku_id、movement_type、quantity、reference_id` |
| `price_histories` | SKU 历史价格 | `sku_id、old_price、new_price、changed_at` |
| `orders` | 订单主表 | `shop_id、order_no、status、paid_amount、paid_at、created_at` |
| `order_items` | 订单商品明细及销量统计 | `order_id、sku_id、quantity、unit_price、paid_amount` |

`inventory` 保存当前值，`inventory_movements` 解释库存如何变化，两者不能混为一谈。库存预占和释放应定义明确的状态与流水；“通知仓库”只是异步任务，不等于库存预占。

### 4.3 促销、退款与审批

| 表 | 职责 | 关键字段或关系 |
| --- | --- | --- |
| `promotions` | 促销活动主记录 | `shop_id、name、version、status、starts_at、ends_at、created_by` |
| `promotion_items` | 参与活动的 SKU | `promotion_id、sku_id、original_price、discount_rate、promotion_price` |
| `refunds` | 退款申请与执行 | `order_id、refund_no、amount、reason、status` |
| `approvals` | 高风险业务操作审批 | `action_type、resource_type、resource_id、payload_snapshot、status、requester_id、approver_id` |

**促销状态与执行约束**

| 当前状态 | 动作与结果 |
| --- | --- |
| 待审批 `pending_approval` | 审批通过进入 `approved`，驳回进入 `rejected`，申请人撤回进入 `cancelled`。后端拒绝自审、跨店审批及对已处理审批单的再次裁决。 |
| 已批准 `approved` | 复核通过后将同一活动更新为 `scheduled`。价格变化、库存低于门槛、SKU 下架、开始时间已过或时段冲突均进入 `failed`，记录原因。临时系统故障保留已批准状态，可按原操作重试。 |
| 已排期 `scheduled` | 到开始时间进入 `active`，到结束时间进入 `completed`。调度恢复时若整个时段已过，直接结束并记录错过生效时段。 |

审批快照绑定活动 ID 和方案版本，保存店铺、SKU、原价、折扣、活动价、时段及库存门槛。库存变化后仍达标可继续执行。驳回、撤回或业务复核失败后，运营修改并确认方案时，在同一活动上递增版本，重新进入待审批并生成新审批单。旧审批单保留，执行请求必须匹配当前版本。

同店同 SKU 的已排期或生效活动占用时段，首尾相接可排期。所有排期入口都在同一事务内先锁定目标 SKU，再检查价格、库存与时段冲突并更新活动状态。两份独立获批且时段重叠的方案并发执行时，至多一份排期成功，另一份显示冲突活动编号。

退款参考状态：

```text
pending_approval → approved → processing → succeeded
                 ├→ rejected
                 └→ failed（最终允许的路径由状态机定义）
```

状态流转由后端状态机控制，LLM 不能任意修改。`approvals` 不绑定死在 Agent 任务上，普通后台页面发起的操作也应能复用审批机制。审批后执行时仍需重新查询业务状态，不能只信任审批前的快照。

### 4.4 Agent 执行记录

| 表 | 职责 | 关键字段或关系 |
| --- | --- | --- |
| `agent_sessions` | 一次运营对话 | `user_id、shop_id、title、created_at` |
| `agent_messages` | 对话消息 | `session_id、role、content、created_at` |
| `agent_tasks` | 一次业务任务 | `session_id、task_type、status、llm_provider、model_name、input_json、plan_json` |
| `agent_tool_calls` | 工具调用及排障依据 | `task_id、tool_name、request_json、response_summary、status、error` |

`agent_messages` 是对话，`agent_tasks` 是业务任务，`agent_tool_calls` 记录 Agent 实际查询和调用过什么。工具响应只保存必要摘要，避免敏感数据扩散和记录膨胀。

### 4.5 执行可靠性、幂等与审计

| 表 | 职责 | 关键字段或关系 |
| --- | --- | --- |
| `operation_requests` | 业务操作幂等与执行结果 | `organization_id、operation_type、idempotency_key、request_hash、status、resource_id` |
| `jobs` | 后台任务 | `job_type、status、payload、attempts、available_at` |
| `outbox_events` | 可靠异步事件投递 | `event_type、aggregate_id、payload、status` |
| `audit_logs` | 不可变操作审计 | `actor_id、action、resource_type、resource_id、before_json、after_json、created_at` |

建议对 `(organization_id, operation_type, idempotency_key)` 建唯一约束。相同幂等键但请求内容不同，应返回冲突，不能复用旧结果。`jobs` 表示异步执行单元；一个 Agent 任务可以关联多个审批、操作请求和后台任务。

## 5. 核心关系

```text
Organization → Shop → Product → SKU → Inventory
                             └→ OrderItem → Order

AgentSession → AgentTask → AgentToolCall
                     ├→ Approval
                     ├→ OperationRequest → Promotion / Refund
                     └→ Job

所有关键操作 → AuditLog
```

## 6. 后端工程能力的验证点

- **事务与一致性**：同一业务操作的多表写入原子提交，失败整体回滚。
- **幂等**：重复点击、Agent 重试和超时重试不会重复创建促销或退款。
- **状态机**：非法状态流转被服务端拒绝。
- **并发控制**：库存和促销执行处理并发修改，执行价格不采用 Agent 的旧数据。
- **权限与多租户**：不能跨组织或跨店铺读取、审批和操作。
- **可靠异步任务**：RabbitMQ、Outbox 与消费幂等保证任务可重试、可追踪，重复投递不会重复执行业务。
- **审批与审计**：可以追溯谁发起、谁批准、执行前后发生了什么。

首个业务切片优先实现事务、幂等、状态机、权限、审批和审计；随后实现 RabbitMQ 可靠任务链路与库存并发控制。

## 7. 前端设计

### 7.1 页面地图

```text
登录
└── 运营工作台
    ├── Agent 对话
    │   ├── 消息与方案卡片
    │   ├── 数据依据与风险说明
    │   └── 任务执行时间线
    ├── 商品运营
    │   ├── 商品/SKU 列表与详情
    │   ├── 库存与流水
    │   └── 调价记录
    ├── 促销管理
    │   ├── 活动列表与详情
    │   ├── 促销日历
    │   └── 活动复盘
    ├── 订单售后
    │   ├── 订单列表与详情
    │   └── 退款记录与详情
    ├── 经营分析
    │   ├── 指标看板
    │   ├── 分析报告
    │   └── 库存周转与异常指标
    └── 系统管理
        ├── 审批中心
        ├── 任务中心
        ├── 通知中心
        ├── 知识库管理
        ├── 用户与店铺权限
        ├── 审计与工具调用记录
        └── 沙箱管理（仅本地沙箱的组织管理员）
```

页面围绕运营任务设计，不为每张数据库表建立独立 CRUD 页面。

### 7.2 核心交互

- 仅面向桌面端工作台场景（Desktop-only），不做移动端布局与响应式断点适配。
- Agent 对话区支持流式输出，结构化方案以卡片呈现。
- 缺少必要信息时明确追问，不直接执行高风险操作。
- 方案卡片展示销量、库存、价格依据、活动时间和风险提示。
- 审批中心支持查看参数快照、数据依据、通过与驳回。
- 任务详情展示工具调用、状态流转、后台任务和审计时间线。
- 交互状态原型见 `prototypes/png/`：切店、校验拦截、权限拒绝、幂等返回、修订再审和重试通过现有页面的状态、详情或弹层呈现，不新增同名一级菜单。
- 表格、筛选、表单和详情页使用 Ant Design；Agent 方案卡片和时间线按需自定义。

### 7.3 前端技术栈

| 类型 | 选型 |
| --- | --- |
| 框架与语言 | React、TypeScript |
| 构建工具 | Vite |
| UI 组件库 | Ant Design 5 |
| 路由 | React Router |
| 服务端状态 | TanStack Query |
| 图表 | Apache ECharts |
| 样式 | Ant Design Design Token、CSS Modules |
| 测试 | Vitest |

第一版不同时引入 Tailwind、shadcn/ui 等多套样式或组件体系。

### 7.4 视觉风格基线

前端界面以 `DESIGN.md` 轻交易轨道为视觉基线（桌面端静态原型见 `prototypes/design-md.html`）：

- **色彩与表面**：奶油白画布（`#FBFBF5`）、纯白卡片表面（`#FFFFFF`）、极细浅灰边框（`#E4E4E7`）、纯黑主按钮与主文字（`#000000`）、芦荟/开心果绿关键高亮与状态胶囊（`#C1FBD4` / `#D4F9E0`）。
- **形态与排版**：主要行动按钮与状态标签采用胶囊圆角（`9999px`），卡片采用柔和圆角（`12px`）与克制层级；正文使用 `Inter Variable`（开启 `ss03`），金额与计数统一等宽数字（`tabular-nums`）。
- **工程落地**：保持仅使用 Ant Design 5，通过 `ConfigProvider` Design Token 覆写全局主题并配合局部 CSS Modules 实现 Agent 方案卡片与任务时间线，不引入 Tailwind 或第二套组件库。

## 8. 后端与 Agent 架构

### 8.1 模块化单体

```text
API 层
├── Agent API
├── 商品与库存 API
├── 促销 API
├── 订单与退款 API
├── 分析 API
└── 管理与审批 API

应用服务层
├── Agent 编排与工具
├── 商品/库存应用服务
├── 促销应用服务
├── 订单/退款应用服务
├── 经营分析服务
├── 审批与权限服务
└── 任务与通知服务

领域与基础设施层
├── 业务规则与状态机
├── 幂等、事务和并发控制
├── PostgreSQL 持久化
├── RabbitMQ、Outbox、Worker 与 Scheduler
├── Redis 缓存与短期协调
├── 模型网关、混合检索、Rerank 与 RAG
└── 审计、结构化日志和可观测性
```

保持模块化单体，不拆微服务。Agent 工具和前端 API 复用应用服务，但 Agent 不能调用审批通过接口或直接访问数据库。

### 8.2 后端技术栈

| 类型 | 选型 |
| --- | --- |
| Web 框架 | FastAPI |
| 数据校验 | Pydantic |
| ORM 与驱动 | SQLAlchemy 2.x、异步 Session、asyncpg |
| 数据迁移 | Alembic |
| 数据库 | PostgreSQL |
| 消息与异步任务 | RabbitMQ、PostgreSQL Outbox、独立 Consumer/Worker |
| 定时调度 | 独立 Scheduler，扫描到期业务并写入 Outbox |
| 缓存与短期协调 | Redis |
| 认证 | JWT Access Token、HttpOnly Refresh Token Cookie |
| 密码哈希 | Argon2 |
| 测试 | pytest、pytest-asyncio、HTTPX、testcontainers |
| 代码质量 | Ruff、mypy |
| 部署 | Docker Compose、Uvicorn |

具体依赖版本在安装时锁定，设计文档不预设未经验证的最新版本。

### 8.3 RabbitMQ 可靠任务链路

```text
业务请求
→ PostgreSQL 事务
→ 写业务数据、任务状态、Outbox、审计
→ Outbox Publisher 投递 RabbitMQ
→ Consumer 执行业务任务
→ 幂等更新任务与业务状态
→ 数据库事务提交后 ACK
```

- Publisher 使用持久化消息和投递确认，Consumer 使用手动 ACK。
- Consumer 以 `event_id` 或 `operation_id` 幂等，接受至少一次投递并处理重复消息。
- 临时失败采用有上限的退避重试；超过上限进入 DLX/DLQ，并支持人工处理。
- 消费过程中发生异常时不确认成功，避免任务丢失。
- 促销排期等长期事实保存在 PostgreSQL；Scheduler 到期扫描，不使用 RabbitMQ 延迟消息代替业务状态。
- Redis 不再作为消息队列，也不再使用 Dramatiq。

### 8.4 Agent 与模型链路

| 类型 | 选型 |
| --- | --- |
| Agent 编排 | LangGraph |
| 模型抽象与工具 | LangChain |
| 对话模型渠道 | `.env` 配置切换，统一走 OpenAI 兼容接口（`ChatOpenAI`）：阿里百炼（如 `qwen-plus` / `qwen-max`）与第三方 OpenAI 中转站（如 `gpt-4.1-mini` / `gpt-4.1`） |
| 结构化输出 | Pydantic Schema；按渠道能力使用 `json_schema` 或 `function_calling` 兜底并经后端强校验 |
| Embedding | 固定单一模型与向量维度（如百炼 `text-embedding-v3`/`v4` 或中转站 `text-embedding-3-small`），通过 `.env` 配置，建索引后不随对话模型随意更换 |
| 向量存储 | PostgreSQL、pgvector |
| 关键词检索 | PostgreSQL 全文检索或 pg_search，部署验证后确定 |
| 候选合并 | RRF |
| 精排 | 阿里百炼 Rerank 或兼容 Jina/Cohere Rerank 的 API，通过环境变量配置 |
| 可观测性 | LangSmith、OpenTelemetry |
| 日志与审计 | 结构化 JSON 日志；业务审计独立存 PostgreSQL |

双模型渠道统一由后端 Model Gateway 基于 OpenAI 兼容协议封装，不单独引入多套厂商 SDK，也不在前端运营界面暴露模型切换：

- 在 `.env` 中预置阿里百炼（`dashscope`）与第三方 OpenAI 中转站（`openai_relay`）各自的 `BASE_URL`、`API_KEY` 和默认/高阶模型名，通过单一开关 `LLM_PROVIDER=dashscope|openai_relay` 切换生效。
- 任务执行时自动将当前后端生效的 `llm_provider` 与 `model_name` 写入 `agent_tasks`，用于审计与效果对比；前端无需感知模型渠道。
- 不同模型与中转站在 Strict Structured Output 和 Tool Calling 行为上存在差异，Model Gateway 按渠道配置兼容模式，所有关键参数、金额计算与权限规则仍由后端确定性代码兜底。
- 修改 `.env` 切换对话模型不影响业务表，但知识库 `pgvector` 的 Embedding 模型与维度在建索引后必须固定；如需更换 Embedding 渠道，需全量重建向量索引或按模型版本隔离存储，避免不同向量空间混检。

```text
用户消息
→ 加载用户、组织、店铺和权限上下文
→ LangGraph 识别任务类型
→ 查询工具获取数据库事实
→ 规则引擎校验业务条件
→ 生成结构化执行方案
→ 信息不足则追问 / 只读任务则直接回答
→ 用户确认后提交审批
→ 有权限的审批人批准当前版本
→ 确定性业务服务执行
→ 查询进度并反馈结果
```

LLM 不负责计算最终促销价、判断权限、修改业务状态或执行退款。

### 8.5 知识库检索与 Rerank

```text
用户问题
→ 组织与文档权限过滤
→ 关键词检索 + pgvector 向量检索
→ RRF 合并候选文档
→ Rerank 模型精排
→ Top N 文档进入 LLM
→ 生成带引用的答案
```

检索结果保留 `document_id、chunk_id、文档版本和引用位置`。Rerank 只用于运营规则、政策和文档问答，不参与库存、订单等业务事实查询。

## 9. 分阶段实施计划

| 阶段 | 交付内容 | 验证点 |
| --- | --- | --- |
| 0 | 完整设计、测试数据、工程骨架与双模型渠道连通性 | 业务规则、模块边界、工程环境及百炼/中转站连通性可验证 |
| 1 | 组织权限、商品、库存和订单基础数据 | 数据可初始化，越权访问被拒绝 |
| 2 | 促销、审批、幂等、审计和基础 Agent | 从自然语言指令到审批后排期，可追溯申请人、审批人和执行结果。重复提交同一版本只有一张审批单，重复执行只有一个已排期活动。按第 4.3 节验证独立方案的并发冲突。 |
| 3 | RabbitMQ、Outbox、Worker、Scheduler、重试和通知 | 到点生效、到期结束。模拟 Worker 提交数据库事务后、ACK 前中断，重投后仍只有一次业务状态变更，任务结果可查。 |
| 4 | 退款、订单售后 Agent 和库存并发 | 重复退款、非法流转与并发问题被拒绝 |
| 5 | 调价、经营分析、混合检索、Rerank 和活动复盘 | 查询有依据，分析口径和引用明确 |
| 6 | 故障模拟、评测、部署和演示优化 | 失败可恢复，项目可重复部署与演示 |

**阶段 2 的 Agent 对照验收**

使用第 2.7 节的固定数据，同时检查工具调用记录和数据库结果。

| 运营输入与前置条件 | 预期工具行为与可见结果 |
| --- | --- |
| “查一下 SKU-001 最近 7 天销量” | 仅调用当前店铺的销量查询，回答 120 件及统计区间，促销表与审批表均无新增。 |
| 第 2.7 节示例指令，未给开始日期 | 查询本店业务事实并追问开始日期。补充日期后展示 ¥159.20 和完整时段，等待确认。两个回合均无促销或审批写入。 |
| 已展示当前店铺的完整方案，用户说“确认刚才的方案” | 调用提交审批工具，返回待审批状态及审批单编号。再次确认返回同一编号。换店后确认旧方案应提示切回原店，两个店铺均无新增审批单。 |

## 10. 工程目录约定

项目采用前后端同仓（Monorepo）组织，目录按阶段渐进补齐，不在阶段 0 预建无代码的空目录。

```text
ecommerce-agent/
├── docs/                           # 产品与架构设计文档
├── prototypes/                     # 桌面端视觉原型参考
├── docker-compose.yml              # PostgreSQL(pgvector) + Redis + RabbitMQ 本地编排
├── .env.example                    # 含 LLM_PROVIDER=dashscope|openai_relay 及双渠道配置模板
│
├── backend/                        # Python + FastAPI 模块化单体（API / Worker / Scheduler 共用）
│   ├── pyproject.toml              # 依赖与 Ruff / mypy / pytest 配置
│   ├── alembic/                    # 数据库迁移脚本
│   ├── scripts/
│   │   └── seed.py                 # 固定沙箱数据与边界场景初始化（可重复执行）
│   ├── app/
│   │   ├── main.py                 # FastAPI 启动入口与路由挂载
│   │   ├── core/                   # 配置(.env)、DB/Redis 连接、JWT+Argon2 认证、租户/店铺上下文、日志
│   │   ├── models/                 # SQLAlchemy ORM 表模型（按域分文件：org/catalog/promotion/approval/agent/reliability）
│   │   ├── schemas/                # Pydantic 请求响应与结构化方案 Schema
│   │   ├── services/               # 确定性应用服务层（业务规则、状态机、锁行并发、幂等、审计、CommerceAdapter）
│   │   ├── api/                    # HTTP 路由层（薄层：鉴权 + 校验 + 调用 services）
│   │   ├── agent/                  # LangGraph + LangChain 编排层
│   │   │   ├── llm_gateway.py      # 统一 ChatOpenAI 封装，按 .env 切换百炼/中转站与结构化输出兜底
│   │   │   ├── graph.py            # 状态图与多轮追问编排
│   │   │   ├── tools.py            # 权限受控工具（只调 services，禁止直连 DB 与审批通过）
│   │   │   └── rag/                # [阶段 5] 切片、pgvector 混合检索、RRF、Rerank
│   │   └── workers/                # [阶段 3] RabbitMQ 拓扑、Outbox Publisher、Consumer、Scheduler 入口
│   └── tests/
│       ├── unit/                   # 状态机、价格/时段计算、规则边界单测
│       ├── integration/            # 真实 PostgreSQL 事务、幂等、并发冲突、越权集成测试
│       └── agent/                  # 阶段 2 对照验收（工具调用与无副作用校验）
│
└── frontend/                       # React + TypeScript + Vite + Ant Design 5（Desktop-only）
    ├── package.json
    ├── vite.config.ts
    ├── tests/                      # 独立前端测试目录（Vitest），不与 src 业务文件混放
    └── src/
        ├── main.tsx
        ├── router.tsx              # 路由与登录态守卫
        ├── theme/
        │   └── designTokens.ts     # DESIGN.md 视觉基线的 Ant Design ConfigProvider Token
        ├── api/                    # HTTP 客户端（Refresh Cookie 续期、幂等键）与 TanStack Query hooks
        ├── layouts/                # 桌面端固定外壳（顶栏 + 左侧导航 + 店铺上下文）
        ├── components/             # 自定义业务组件 + CSS Modules（方案卡片、任务时间线、状态胶囊）
        └── pages/                  # 按第 7.1 节页面地图演进（首期：login/workspace/promotions/approvals/tasks）
```

目录边界约束：

- `backend/app/api/` 与 `backend/app/agent/tools.py` 只能调用 `backend/app/services/`，不得绕过应用服务层直接修改业务表；`tools.py` 不暴露审批通过操作。
- Web API（`uvicorn app.main:app`）、Consumer（`python -m app.workers.consumer`）与 Scheduler（`python -m app.workers.scheduler`）共用 `backend/` 代码包，通过不同进程命令启动，避免业务规则与状态机漂移。
- 前后端测试代码分别集中存放在 `backend/tests/` 与 `frontend/tests/`，不与业务源码放在同一目录。
- `workers/`（阶段 3）、`agent/rag/`（阶段 5）及非首期前端页面在对应阶段落地时再创建。

