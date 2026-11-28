import logging
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import AppSettings, LLMChannelSettings
from app.schemas.agent import ORDER_NO_PATTERN, SKU_PATTERN, QueryIntent
from app.schemas.knowledge import KnowledgeCitationItem

logger = logging.getLogger(__name__)

# 按渠道配置缓存 ChatOpenAI 客户端，避免每次请求重建 HTTP 连接池
_CLIENT_CACHE: dict[tuple[str, str, str, str], ChatOpenAI] = {}

SYSTEM_PROMPT = (
    "你是电商运营 Agent 的意图与槽位识别器，只做分类和参数抽取，严禁编造价格、库存、订单或销量数据。\n"
    "按以下顺序匹配：\n"
    "1. 当用户明确确认刚才生成的促销、退款或调价方案并要求提交审批（如“确认刚才的方案”“确认提交”“提交审批”），"
    "返回 kind='confirm_plan'。注意：如果用户要求直接批准/通过/驳回审批单，"
    "或直接修改库存、售价或订单状态，返回 kind='unsupported'。\n"
    "2. 当用户询问平台/店铺运营规则、审批后复核规范、退款/重复退款政策、调价/库存预警制度或操作手册等"
    "纯制度规则问题（例如“审批通过之后，如果 SKU 的售价变了，系统还能按原方案排期吗？”"
    "“已有退款处理中还能再申请吗？”，不涉及具体 SKU 编码实时销量/调价/促销操作，也不涉及具体订单号查单/退款），"
    "返回 kind='knowledge_qa'。\n"
    "3. 为单个 SKU 策划、预览或创建折扣促销（可附带查询该 SKU 的近 7 天销量或库存），返回 kind='promotion'。\n"
    "   - 若消息包含单一折扣（如 8 折、打八折、8.5 折），抽取数值到 discount（如 8、8.5）；\n"
    "   - 若消息包含单一整数持续天数（如 持续 3 天、做三天），抽取整数到 duration_days"
    "（排除“最近 7 天销量”这类查询窗口）；\n"
    "   - 若消息包含明确开始日期（如 11 月 22 日开始、2026-11-22），抽取日期文本或 YYYY-MM-DD"
    " 到 start_date；缺少开始日期仍归为 promotion；\n"
    "   - 若消息包含多组折扣/天数/日期区间、半天/小数天、具体时钟点（如上午10点）"
    "或自定义库存件数门槛，设置 ambiguous_terms=true。\n"
    "3. 为单个 SKU 评估调整常规售价、预览调价或发起调价申请"
    "（如 调价、改价、将售价调到/改为某金额），返回 kind='price_change'。\n"
    "   - 抽取原样出现的 SKU 编码到 sku_code；\n"
    "   - 若明确给出目标售价（如 调到 189 元），抽取目标价到 target_price；\n"
    "   - 若明确给出调价原因（如 原因调整冬季常规售价），抽取简要原因到 price_change_reason。\n"
    "4. 为某笔订单（如 ORD-20261111-01 / DEMO-20261120）核验退款资格、预览退款或申请退款，返回 kind='refund'。\n"
    "   - 抽取消息中原样出现的订单号到 order_no（缺失时返回 null）；\n"
    "   - 若明确给出退款金额（如 退 50 元、¥89.00），抽取金额数值到 refund_amount；\n"
    "   - 若明确给出退款原因（如 原因包装破损、部分商品售后），抽取简要原因到 refund_reason。\n"
    "5. 仅查询某笔订单状态/售后资格，或筛选店铺异常订单并查看处理建议，返回 kind='order_query'。\n"
    "   - 若针对特定订单查询，抽取 order_no；若筛选异常订单/待复核订单，设置 exception_only=true。\n"
    "6. 仅查询单个 SKU 最近 7 天的销量或销售件数，返回 kind='sales'；不指定天数时默认 7 天。\n"
    "7. 明确要求批准/通过审批、排期执行、直接扣减/预占库存或直接修改售价/订单状态、其他销量时间范围、"
    "多 SKU 或其他任务，返回 kind='unsupported'。\n"
    "sku_code 与 order_no 必须在消息中原样出现；缺失时返回 null。"
)


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ClassificationResult:
    intent: QueryIntent
    llm_provider: str
    model_name: str


def clear_client_cache() -> None:
    _CLIENT_CACHE.clear()


def _get_or_create_client(channel: LLMChannelSettings) -> ChatOpenAI:
    cache_key = (channel.provider, channel.base_url, channel.api_key, channel.default_model)
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = ChatOpenAI(
            model=channel.default_model,
            base_url=channel.base_url,
            api_key=SecretStr(channel.api_key),
            timeout=15,
            max_retries=0,
            temperature=0,
        )
        _CLIENT_CACHE[cache_key] = client
    return client


async def classify_with_metadata(
    message: str,
    *,
    settings: AppSettings | None = None,
) -> ClassificationResult:
    resolved_settings = settings if settings is not None else AppSettings.load()
    channel = resolved_settings.active_llm_channel()
    if channel is None:
        raise ModelUnavailable

    try:
        # 仅在使用真实 ChatOpenAI 类时走缓存，兼容单元测试对 ChatOpenAI 的 monkeypatch
        client = (
            _get_or_create_client(channel)
            if settings is not None
            else ChatOpenAI(
                model=channel.default_model,
                base_url=channel.base_url,
                api_key=SecretStr(channel.api_key),
                timeout=15,
                max_retries=0,
                temperature=0,
            )
        )
        classifier = client.with_structured_output(QueryIntent, method=channel.structured_output_mode)
        result = await classifier.ainvoke([
            ("system", SYSTEM_PROMPT),
            ("human", message),
        ])
        return ClassificationResult(
            intent=QueryIntent.model_validate(result),
            llm_provider=channel.provider,
            model_name=channel.default_model,
        )
    except Exception as error:
        logger.warning(
            "Agent intent provider failed: provider=%s model=%s error=%s",
            channel.provider,
            channel.default_model,
            type(error).__name__,
        )
        raise ModelUnavailable from None


def _build_deterministic_knowledge_summary(citations: Sequence[KnowledgeCitationItem]) -> str:
    top = citations[0]
    first_line = top.content.strip().splitlines()[0].strip()
    summary = (
        f"依据《{top.title}》（{top.doc_no} V{top.document_version}"
        f" · {top.section_title} · {top.page_label}）：{first_line}"
    )
    if len(citations) > 1:
        second = citations[1]
        second_line = second.content.strip().splitlines()[0].strip()
        summary += f"\n补充参考《{second.title}》（{second.section_title}）：{second_line}"
    return summary


async def synthesize_knowledge_answer(
    question: str,
    citations: Sequence[KnowledgeCitationItem],
    *,
    settings: AppSettings | None = None,
) -> str:
    if not citations:
        return "当前知识库中暂未检索到当前角色权限可见的匹配规则条款，请先确认规则文档已完成索引。"

    resolved_settings = settings if settings is not None else AppSettings.load()
    channel = resolved_settings.active_llm_channel()
    if channel is None:
        return _build_deterministic_knowledge_summary(citations)

    evidence_blocks = "\n\n".join(
        f"[{idx}] {item.doc_no}《{item.title}》V{item.document_version} · {item.section_title}（{item.page_label}）\n"
        f"{item.content}"
        for idx, item in enumerate(citations, start=1)
    )
    system_prompt = (
        "你是电商运营规则助手。请仅基于下方检索到的运营/售后规则片段，用简洁、明确的中文直接回答用户问题。\n"
        "严格要求：\n"
        "1. 先给出明确结论，再结合条款要点作 2-3 句简明说明，并标明出处章节或页码；\n"
        "2. 严禁编造片段之外的规则、价格、库存、订单金额或商品状态；\n"
        "3. 不要声称已查询实时业务库或已发起审批/改价/退款操作。"
    )
    try:
        client = (
            _get_or_create_client(channel)
            if settings is not None
            else ChatOpenAI(
                model=channel.default_model,
                base_url=channel.base_url,
                api_key=SecretStr(channel.api_key),
                timeout=15,
                max_retries=0,
                temperature=0,
            )
        )
        response = await client.ainvoke([
            ("system", system_prompt),
            ("human", f"问题：{question}\n\n已检索规则片段：\n{evidence_blocks}"),
        ])
        raw_content = getattr(response, "content", "")
        text = raw_content.strip() if isinstance(raw_content, str) else ""
        return text or _build_deterministic_knowledge_summary(citations)
    except Exception as exc:
        logger.warning(
            "Knowledge answer synthesis fell back to deterministic summary: provider=%s error=%s",
            channel.provider,
            type(exc).__name__,
        )
        return _build_deterministic_knowledge_summary(citations)


async def classify(message: str, *, settings: AppSettings | None = None) -> QueryIntent:
    return (await classify_with_metadata(message, settings=settings)).intent


__all__ = [
    "ClassificationResult",
    "ModelUnavailable",
    "ORDER_NO_PATTERN",
    "SKU_PATTERN",
    "classify",
    "classify_with_metadata",
    "clear_client_cache",
    "synthesize_knowledge_answer",
]
