# CommerceAgent · 电商运营 Agent

CommerceAgent 是一个仅面向桌面端的可控执行型电商运营 Agent 项目。它的目标不是实现完整电商平台，而是打通一条可演示、可验证的业务闭环：

```text
自然语言指令 → 业务数据查询 → 执行方案生成 → 风险与权限校验
→ 人工审批 → 业务执行 → 失败重试与补偿 → 全程审计
```

## 项目状态

当前仓库处于**设计阶段**，包含：

- 产品定位、业务规则、核心表结构与分阶段计划的[产品设计](docs/product-design.md)。
- 视觉规范 [DESIGN.md](DESIGN.md)。
- 68 张桌面端 UI 原型图与阅读索引，见 [prototypes/README.md](prototypes/README.md)。

后端、前端与数据库尚未实现，当前没有可运行的启动命令。

## 核心设计

- **LLM 不直接修改数据库**：模型负责理解意图、分析数据与生成结构化方案。
- **Agent 工具受控**：所有业务访问通过权限受控的服务层完成。
- **高风险操作必须审批**：促销、调价、退款等操作提交后进入人工审批。
- **执行前重新校验**：审批通过后仍会复核价格、库存、SKU 状态、活动时段与权限。
- **事实数据来自数据库**：销量、库存、订单和价格由查询结果计算，不由模型编造。
- **全程可追踪**：工具调用、审批、执行状态和审计记录关联到同一任务与资源。

## 技术规划

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI、SQLAlchemy 2.x、Pydantic、pytest |
| 数据库 | PostgreSQL、Alembic |
| 异步任务 | RabbitMQ、PostgreSQL Outbox、Worker、Scheduler |
| Agent | LangGraph、LangChain、OpenAI 兼容接口 |
| 前端 | React、TypeScript、Vite、Ant Design 5、Vitest |

双模型渠道（DashScope / OpenAI 中转站）仅通过后端 `.env` 配置切换，前端不提供模型切换控件。

## 实施路线

1. **阶段 0–1**：工程骨架、组织权限、商品、库存、订单基础数据。
2. **阶段 2**：促销、审批、幂等、审计和基础 Agent 闭环。
3. **阶段 3**：可靠异步任务、重试、补偿与通知。
4. **阶段 4–5**：退款、调价、经营分析、知识库与活动复盘。
5. **阶段 6**：故障模拟、评测、部署与演示优化。

首版重点先完成促销闭环：运营输入、Agent 追问、方案生成、人工审批、执行复核、排期和审计。业务数据使用合成数据初始化到 PostgreSQL，不连接真实店铺、支付或物流。

## 目录结构

```text
.
├── AGENTS.md
├── DESIGN.md
├── README.md
├── docs/
│   └── product-design.md
└── prototypes/
    ├── README.md
    ├── design-md.html
    └── png/
```

## 参与开发

- 修改前先阅读 [AGENTS.md](AGENTS.md)、[docs/product-design.md](docs/product-design.md) 和 [DESIGN.md](DESIGN.md)。
- 后端与 Agent 不允许绕过服务层直接修改业务表。
- 后端测试放在 `backend/tests/`，前端测试放在 `frontend/tests/`。
- 不要提交 `.env`、密钥、本地日志、`.DS_Store` 或 `.playwright-mcp/`。
