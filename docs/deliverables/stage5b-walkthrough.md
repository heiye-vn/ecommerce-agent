# 阶段 5B 交付验证报告：知识库管理、pgvector 混合检索与阿里百炼重排

阶段 5B 已按 [阶段 5 统一实施计划](./stage5-implementation-plan.md) 完成知识库数据层（`pgvector`）、混合检索 + RRF + 阿里百炼 Embedding / Rerank 管道、知识库管理 API、Agent 规则问答链路与桌面端工作台全部交付，并已更新 [README.md](../../README.md)；所有后端检查、迁移与真实百炼向量播种、前端全量测试构建及桌面端只读核验均已通过。

---

## 一、核心交付清单

### 1. 依赖、配置隔离与可回滚迁移（`0010`）
- **依赖接入**：在 [pyproject.toml](../../backend/pyproject.toml) 新增 `httpx>=0.27.0` 与 `pgvector>=0.3.6,<1`（已安装 `pgvector==0.5.1`），不新增额外 Worker 进程。
- **百炼配置独立化**：更新 [config.py](../../backend/app/core/config.py) 与 [.env.example](../../.env.example)，在 `AppSettings` 补充 `dashscope_base_url` 与 `dashscope_api_key`，保证即使对话通道切至中转站，知识库仍固定采用 `qwen3.7-text-embedding-flash`（`1024` 维）与 `qwen3.7-text-rerank`。
- **模型与迁移 `0010`**：新增 [knowledge.py](../../backend/app/models/knowledge.py)（`KnowledgeDocument`、`KnowledgeChunk`）并导出至 [models/__init__.py](../../backend/app/models/__init__.py)；新增可回滚迁移 [0010_knowledge_base_pgvector.py](../../backend/alembic/versions/0010_knowledge_base_pgvector.py)，开启 PostgreSQL `vector` 扩展，建立 `VECTOR(1024)` + HNSW 余弦索引、`TSVECTOR` GIN 索引与组织/编号/版本唯一约束，并将表名校验补充至 [test_database.py](../../backend/tests/integration/test_database.py)。

### 2. RAG 混合检索管道与知识库管理服务/API
- **RAG 核心组件**（`backend/app/agent/rag/`）：
  - [__init__.py](../../backend/app/agent/rag/__init__.py)：导出切片、向量化、精排与混合检索核心能力；
  - [chunker.py](../../backend/app/agent/rag/chunker.py)：识别 Markdown 标题、`§` 条款与 `[第N页]` 标记生成可追溯分片，提取中文 2-gram + 英文数字编码用于 PostgreSQL `'simple'` 词法匹配；
  - [embeddings.py](../../backend/app/agent/rag/embeddings.py)：实现 `DashScopeEmbeddingClient`（`/embeddings`，`qwen3.7-text-embedding-flash`，1024 维，10 条分批及维度强校验）；
  - [rerank.py](../../backend/app/agent/rag/rerank.py)：实现 `DashScopeRerankClient`（`qwen3.7-text-rerank`，10 秒超时保护，异常/超时安全降级保留 RRF 排序并返回 `degraded=True`）；
  - [retriever.py](../../backend/app/agent/rag/retriever.py)：严格前置过滤组织、店铺、当前有效版本（`is_current=True`）、`status='indexed'` 及角色交集，再执行 `pgvector` 余弦召回 + PostgreSQL 词法召回 + RRF 融合 + 百炼 Rerank 精排，按 `embedding_model` / `embedding_dim` 隔离向量空间。
- **服务与 HTTP 接口**：新增 [schemas/knowledge.py](../../backend/app/schemas/knowledge.py)、[services/knowledge.py](../../backend/app/services/knowledge.py) 与 [api/knowledge.py](../../backend/app/api/knowledge.py)，并在 [main.py](../../backend/app/main.py) 注册：
  - `GET /api/shops/{shop_id}/knowledge/documents`、`GET /documents/{document_id}`
  - `POST /api/shops/{shop_id}/knowledge/documents`（支持递增版本发布；事务外调用百炼向量化，向量失败保留 `status='failed'` 且不覆盖既有有效当前版本）
  - `POST /api/shops/{shop_id}/knowledge/documents/{document_id}/reindex`（重建分片与向量并记录 `AuditLog`）
  - `POST /api/shops/{shop_id}/knowledge/search`（只读混合检索 + RRF + Rerank 验证入口）

### 3. Agent 规则问答升级与演练数据初始化
- **Agent 只读规则问答**：扩展 [schemas/agent.py](../../backend/app/schemas/agent.py)、[llm_gateway.py](../../backend/app/agent/llm_gateway.py)、[tools.py](../../backend/app/agent/tools.py)（`search_knowledge_tool`）、[graph.py](../../backend/app/agent/graph.py)、[services/agent.py](../../backend/app/services/agent.py) 与 [api/agent.py](../../backend/app/api/agent.py)，支持 `knowledge_qa` 意图识别、带引用规则摘要合成（及离线兜底），强制输出只读隔离声明“本回答仅解释运营规则，未查询商品实时价格，未提交审批或变更数据。”且严禁写业务表。
- **幂等种子脚本**：新增 [services/seed_stage5b.py](../../backend/app/services/seed_stage5b.py) 与 [scripts/seed_stage5b.py](../../backend/scripts/seed_stage5b.py)，初始化 `DOC-001 ~ DOC-005` 五份规则文档（包含 `indexed`、`indexing` 与 `failed` 样本，其中 `DOC-001 ~ DOC-003` 已实测调用百炼生成 1024 维真实向量）。

### 4. 前端知识库管理与 Agent 引用预览界面
- **导航、路由与查询 Hooks**：在 [App.tsx](../../frontend/src/App.tsx) 左侧「系统管理」新增「知识库管理」（`/knowledge`），更新 [router.tsx](../../frontend/src/router.tsx) 与 [queries.ts](../../frontend/src/api/queries.ts)。
- **知识库管理页**：新增 [knowledge/index.tsx](../../frontend/src/pages/knowledge/index.tsx) 与 [knowledge/index.module.css](../../frontend/src/pages/knowledge/index.module.css)，落地文档筛选表格、版本与状态胶囊、失败文档「重建索引」、右侧「引用预览」与「索引提示」卡片及上传弹窗（使用 `destroyOnHidden`）。
- **Agent 引用交互**：更新 [agent/index.tsx](../../frontend/src/pages/agent/index.tsx) 与 [agent/index.module.css](../../frontend/src/pages/agent/index.module.css)，新增规则问答示例、对话流「引用来源」列表（可点击切换右侧「引用预览」详情与章节页码）、混合检索口径与只读隔离提示条。

---

## 二、自动化测试与构建验证结果

| 检查项 | 执行命令 | 实测结果 |
| --- | --- | --- |
| 后端风格检查 | `cd backend && uv run ruff check app tests scripts` | **All checks passed!** |
| 后端静态类型检查 | `cd backend && uv run mypy app` | **Success: no issues found in 82 source files** |
| 迁移 `0010` 与 `alembic check` | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_database.py`<br>`cd backend && uv run alembic upgrade head` | **3 passed**（含 `knowledge_documents`、`knowledge_chunks`、`Vector(1024)`、`TSVECTOR` GIN、`HNSW` 索引回滚重升核验）；本地 `7600` PG 已升级至 `0010_knowledge_base_pgvector` |
| 后端全量测试（含 pgvector 与 Agent 评测） | `cd backend && TESTCONTAINERS_RYUK_DISABLED=true uv run pytest` | **51 passed, 17 warnings in 41.64s**（新增覆盖 [test_stage5b_rag_clients.py](../../backend/tests/unit/test_stage5b_rag_clients.py)、[test_agent_gateway.py](../../backend/tests/unit/test_agent_gateway.py)、[test_stage5b_knowledge_rag.py](../../backend/tests/integration/test_stage5b_knowledge_rag.py) 与 [test_stage5b_knowledge_agent.py](../../backend/tests/agent/test_stage5b_knowledge_agent.py)；17 条 warning 来自第三方 `langsmith` 的 `asyncio.iscoroutinefunction` 弃用提示） |
| 阶段 5B 种子脚本与百炼 1024 维实测 | `cd backend && uv run python -m scripts.seed_stage5b`（连跑两次） | 首次返回 `{'DOC-001': True, 'DOC-002': True, 'DOC-003': True, 'DOC-004': True, 'DOC-005': True}`（无向量降级警告，实测调用 `qwen3.7-text-embedding-flash` 写入 1024 维向量）；二次全部返回 `False` |
| 前端 Vitest 单元测试 | `cd frontend && npm test -- --run` | **Test Files 10 passed (10)，Tests 39 passed (39)**（Duration `16.65s`；含新增 [stage5b_knowledge.test.tsx](../../frontend/tests/stage5b_knowledge.test.tsx)） |
| 前端 TypeScript 与生产构建 | `cd frontend && npm run build` | **`tsc && vite build` 成功（0 错误，built in 334ms）** |

---

## 三、桌面端浏览器核验与进程状态提示

遵循不擅自启停用户本地后台进程的原则，复用本机运行中的服务执行只读浏览器核验（经 `BROWSER_HOST_FACTS` 确认访问 `http://127.0.0.1:7630` 与 `http://127.0.0.1:7620`，`blocked_origins: none`）：

1. **未登录路由守卫与无运行时报错（`http://127.0.0.1:7630/knowledge`）**：
   - 自动重定向至 `http://127.0.0.1:7630/login`，完整渲染“登录工作台”；HUD 控制台报错与页面未捕获异常均为 `0`，仅记录预期的 `POST http://127.0.0.1:7630/api/auth/refresh -> 401 Unauthorized`。
2. **三大桌面视口边界核验（`/login`）**：
   - **`1920x1080`**：`html` / `body` / `#root` 均贴合 `1920x1080`（`main` 为 `1920x1016`），滚轮 `dx = 0, dy = 0`，无溢出与裁剪；
   - **`2560x1440`**：`html` / `body` / `#root` 均贴合 `2560x1440`（`main` 为 `2560x1376`），滚轮 `dx = 0, dy = 0`，无溢出与裁剪；
   - **`1440x900`**：`html` / `body` / `#root` 均贴合 `1440x900`（`main` 为 `1440x836`），滚轮 `dx = 0, dy = 0`，无溢出与裁剪。
3. **后端运行进程实时状态提示（`http://127.0.0.1:7620/openapi.json`）**：
   - 已确认该端口当前已加载阶段 5A（共 `40` 条路径，含 `/price-changes`、`/analytics/` 与 `/promotions/{promotion_id}/review`），尚未包含阶段 5B 新增的 `/api/shops/{shop_id}/knowledge/*` 路由（已查阅最终截图）。
   - **联调前操作提示**：数据库迁移 `0010` 与 `scripts.seed_stage5b` 均已在本机 `7600` 库就绪，请重启后端 `uvicorn app.main:app --host 127.0.0.1 --port 7620` 即可立即体验阶段 5B 知识库管理与 Agent 规则问答功能（若此前仅重启 `uvicorn` 而未重启 `app.workers.consumer`，也请确认 `consumer` 已按阶段 5A 更新）。
