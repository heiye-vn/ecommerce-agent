# 阶段 5 实施计划：调价审批闭环、经营分析、活动复盘与阿里百炼知识库 RAG

> 采取“统一规划、分批实施”策略：先落地并验收 **阶段 5A（调价审批执行闭环 + 经营分析与活动复盘）**，随后实施 **阶段 5B（知识库管理 + `pgvector` 混合检索 + RRF + 阿里百炼 `qwen3.7-text-rerank` 精排 + Agent 规则问答）**。

---

## 一、全局技术约束与决策基线

1. **知识库阿里百炼模型选型（阶段 5B，配置项于 5A 预先铺设）**：
   - **Embedding 模型**：`DASHSCOPE_EMBEDDING_MODEL=qwen3.7-text-embedding-flash`，固定维度 `DASHSCOPE_EMBEDDING_DIMENSIONS=1024`，通过 `DASHSCOPE_BASE_URL` 下的 OpenAI 兼容 `/embeddings` 接口调用，复用既有 `DASHSCOPE_API_KEY`。向量索引列固定为 `VECTOR(1024)` 且随表记录模型名与维度，更换向量模型前必须显式重建索引，绝不跨向量空间混检。
   - **Rerank 模型**：`DASHSCOPE_RERANK_MODEL=qwen3.7-text-rerank`，经由 `DASHSCOPE_RERANK_URL`（默认 `https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank`）异步 HTTP 请求完成候选精排；运行期若精排服务暂态超时或不可用，自动记录告警并安全退化回 RRF 排序结果。
   - **严格隔离原则**：知识库混合检索与 Rerank 仅服务运营/售后规则问答，**绝不参与**商品价格、库存、销量或订单等数据库事实查询。
2. **经营分析口径（选项 A，100% 数据库事实）**：
   - 第 3 张核心指标卡采用由数据库真实聚合计算的**客单价（支付销售额 ÷ 支付订单数）**，不引入合成流量访问表，严守“事实数据来自数据库、绝不编造数字”，同时清晰区分“数据事实”与“模型/分析推测”。
3. **进程与边界约束**：
   - 不新增第 4 个常驻后台 Worker 进程，复用 `app.workers.outbox_publisher`、`app.workers.consumer`、`app.workers.scheduler`；
   - 阶段 5A 前端仅引入 Apache ECharts（`echarts`），后端不新增第三方包；阶段 5B 引入 `pgvector` 与主依赖 `httpx`；
   - `api/` 与 `agent/tools.py` 一律经由 `services/` 操作数据库，Agent 绝不暴露审批裁决或直接改价/改库能力。

---

## 二、阶段 5A 实施规划（调价闭环 + 经营分析 + 活动复盘）

### 2.1 数据库模型与可回滚迁移（`0009_price_changes_and_analytics.py`）
- **新增 `price_change_requests` 表（[price_change.py](../../backend/app/models/price_change.py)）**：
  - `organization_id`、`shop_id`、`sku_id`、`request_no`（店内唯一 `PRC-YYYYMMDD-NN`）、`old_price`、`new_price`、`cost_price`、`reason`、`status`（`pending_approval / approved / processing / succeeded / rejected / cancelled / failed`）、`requester_id`、`failure_reason`、`executed_at`、`updated_at`；
  - 复合外键 `(organization_id, shop_id) -> shops(organization_id, id)` 与 `(shop_id, sku_id) -> skus(shop_id, id)`；
  - **部分唯一索引 `uq_price_changes_active_sku`**：`ON price_change_requests(shop_id, sku_id) WHERE status IN ('pending_approval', 'approved', 'processing')`，阻断同 SKU 并发多笔在途调价。
- **扩展既有模型**：
  - [catalog.py](../../backend/app/models/catalog.py) 中 `PriceHistory` 补充 `shop_id`、`actor_id`、`approval_no`、`price_change_request_id`、`reason` 并在迁移中按 `skus.shop_id` 回填历史数据；
  - [reliability.py](../../backend/app/models/reliability.py) 中 `ck_jobs_type` 增加 `'price_change_execute'`，`ck_notifications_category` 增加 `'price_change'`。
- **迁移可回滚保障**：`downgrade()` 先清理阶段 5A 新增的 `price_change_execute` 任务/Outbox 与 `price_change` 通知记录，再收窄约束并回退表结构。

### 2.2 后端确定性服务、RabbitMQ 队列与 Agent 升级
1. **调价闭环（[services/price_changes.py](../../backend/app/services/price_changes.py)、[api/price_changes.py](../../backend/app/api/price_changes.py)）**：
   - `POST /api/shops/{shop_id}/price-changes`：受 `OperatorShop`、`Idempotency-Key`、`reject_cross_origin` 保护；事务内 `FOR UPDATE` 锁 `skus` 行，拦截下架 SKU（`409 SKU_NOT_ACTIVE`）、价格未变化（`422 PRICE_UNCHANGED`）、低于成本线（`422 PRICE_BELOW_COST`）及已有在途调价（`409 PRICE_CHANGE_IN_FLIGHT_EXISTS`），同事务创建 `price_change_requests`、不可变审批快照、`audit_logs` 与审批员站内通知；
   - `POST /api/shops/{shop_id}/price-changes/{request_id}/cancel`：仅原申请人或管理员可撤回 `pending_approval` 申请，同步取消关联审批单；
   - `execute_approved_price_change`（由 Consumer 事务内调用）：再次 `FOR UPDATE` 锁定请求与 SKU，**二次实时复核** SKU 在售状态、目标价不低于成本价、以及**当前售价与审批快照 `old_price` 是否一致**；若原价已变化（对齐原型 `54`），标为 `failed`、记录 `failure_reason`、不改价且不写 `price_histories`；复核通过则更新 `skus.current_price` 并追加不可变 `price_histories`。
2. **审批、任务与 RabbitMQ 泛化**：
   - [approvals.py](../../backend/app/services/approvals.py)、[jobs.py](../../backend/app/services/jobs.py)、[topology.py](../../backend/app/workers/topology.py)、[consumer.py](../../backend/app/workers/consumer.py)：支持 `resource_type="price_change"` 与事件 `price_change.execute.requested`；`TopologyConfig.production()` 增加 `commerce.jobs.price_change` 与 `commerce.jobs.price_change.dlq`，由现有 `app.workers.consumer` 并发监听。
3. **调价 Agent 升级**：
   - 新增 [services/price_change_preview.py](../../backend/app/services/price_change_preview.py)，扩展 [schemas/agent.py](../../backend/app/schemas/agent.py)、[llm_gateway.py](../../backend/app/agent/llm_gateway.py)、[tools.py](../../backend/app/agent/tools.py)、[graph.py](../../backend/app/agent/graph.py)、[services/agent.py](../../backend/app/services/agent.py)；
   - 支持单 SKU 调价缺参追问（无写库）、预览结构化调价方案（含成本线与历史价依据）及 `confirm_plan` 显式确认幂等送审（对齐原型 `39`）。
4. **经营分析与活动复盘 API（[services/analytics.py](../../backend/app/services/analytics.py)、[api/analytics.py](../../backend/app/api/analytics.py)、[api/promotions.py](../../backend/app/api/promotions.py)）**：
   - `GET /api/shops/{shop_id}/analytics/overview`：当期 vs 上期等长窗口实付总额、环比、支付单数、客单价、退款率、按日走势与 SKU 销量排行（含严格计算口径）；
   - `GET /api/shops/{shop_id}/analytics/daily-report`：完整自然日经营日报，结构化拆分数据库事实、分析边界与只读行动建议；
   - `GET /api/shops/{shop_id}/analytics/inventory-turnover`：件数口径库存周转与低库存/退款上升异常提示；
   - `GET /api/shops/{shop_id}/promotions/{promotion_id}/review`：按 `Asia/Shanghai` 自然日对齐活动期与活动前等长基线期销量/销售额对比与复盘说明。
5. **幂等演示数据脚本（[scripts/seed_stage5a.py](../../backend/scripts/seed_stage5a.py)）**：
   - 严守不往 `SKU-001` 的 `2026-11-14 ~ 11-21` 窗口追加支付订单（保持 7 天 `120` 件销量基线）；补充 `SKU-018`、`SKU-002`、上期及多日销售与 1 笔退款、已完成/生效中活动及 `SKU-001` 早期调价历史。

### 2.3 前端桌面端扩展（接入 `echarts`，对齐 `DESIGN.md` 与 `prototypes/png/`）
- **商品运营（[catalog/index.tsx](../../frontend/src/pages/catalog/index.tsx)）**：新增「调价记录」Tab（原型 `09`）及调价执行/复核失败详情页 `/catalog/price-changes/:requestId`（原型 `53`、`54`）；
- **促销管理（[promotions/index.tsx](../../frontend/src/pages/promotions/index.tsx)）**：新增「促销日历」Tab（按店铺时区月历，原型 `13`）与「活动复盘」Tab（ECharts 活动期 vs 基线期曲线对比，原型 `14`）；
- **经营分析（`/analytics`，启用侧栏导航）**：实现三 Tab「指标看板 / 分析报告 / 库存周转与异常」（ECharts 走势图 + 客单价/退款率指标带 + 事实/推测隔离卡片，原型 `20`、`21`、`62`）；
- **审批 / 任务 / 通知 / Agent**：适配 `price_change` 快照审核、任务重试、`price_change` 通知与 Agent 调价方案确认卡片（原型 `39`）。

---

## 三、阶段 5B 实施规划（知识库管理 + pgvector 混合检索 + 阿里百炼 RRF & Rerank）

1. **数据库扩展与迁移 `0010_knowledge_base_pgvector.py`**：
   - 开启 PostgreSQL `vector` 扩展；
   - 新增 `knowledge_documents`（`organization_id`、`shop_id` 可空表全组织可见、`doc_no`、`title`、`version`、`visibility_scope`、`status`、`embedding_model`、`embedding_dim`、`chunk_count`、`last_error`）与 `knowledge_chunks`（`document_id`、`document_version`、`chunk_index`、`section_title`、`page_label`、`content`、`tsv_content` 全文检索列、`embedding VECTOR(1024)` 及 HNSW/IVFFlat + GIN 索引）。
2. **RAG 检索管道（`backend/app/agent/rag/`）**：
   - `embeddings.py`：调用阿里百炼 `/embeddings`（模型 `qwen3.7-text-embedding-flash`，维度 `1024`）执行文档分片批量向量化与查询向量化；
   - `rerank.py`：调用阿里百炼 `text-rerank` 接口（模型 `qwen3.7-text-rerank`）对候选片段精排，超时/报错自动降级回 RRF 分数；
   - `retriever.py`：严格执行租户/店铺/可见范围与 `status = 'indexed'` 过滤 → `pgvector` 余弦距离 Top-K + PostgreSQL 词法检索 Top-K → RRF 融合（`1 / (k + rank)`，`k=60`）→ Rerank 精排 → 输出带 `document_id / chunk_id / document_version / section_title / page_label` 引用的片段，回答明确提示“仅解释运营规则，未修改业务数据”。
3. **管理接口与前端页（`api/knowledge.py`、`/knowledge`、原型 `05`、`28`、`61`）**：
   - 支持文档上传/新增版本、异步或同步构建索引/重建索引、引用预览；
   - 侧栏「系统管理」新增「知识库管理」入口，Agent 对话新增 `knowledge_qa` 意图、引用来源卡片与右侧引用预览。

---

## 四、分阶段验收矩阵

| 批次 | 核心自动化与实测验收门禁 |
| --- | --- |
| **阶段 5A** | 1. 调价幂等、同 SKU 并发互斥 `409`、低于成本 `422`、RabbitMQ `commerce.jobs.price_change` 执行改价写流水、原价漂移触发复核失败且不改价、DLQ 人工重试恢复；<br>2. 经营看板（含客单价）、经营日报、库存周转、活动复盘全数值核对与跨店 `403`；<br>3. Agent 调价缺参追问与确认幂等提交；<br>4. 后端 `ruff` / `mypy` / `pytest` 全绿，前端 `vitest` / `vite build` 0 错，3 视口浏览器实测无溢出与报错。 |
| **阶段 5B** | 1. 阿里百炼 `qwen3.7-text-embedding-flash`（1024 维）入库与 `qwen3.7-text-rerank` 精排客户端单测（含超时降级回 RRF）；<br>2. `pgvector` + 词法混合检索 + RRF + 组织/角色隔离测试（未索引版本与失败版本绝不召回）；<br>3. Agent 规则问答必须携带引用且严禁写入促销/退款/调价/库存表；<br>4. 全栈前后端检查与 3 视口浏览器验证通过。 |
