import logging
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import AppSettings, LLMChannelSettings
from app.schemas.agent import SKU_PATTERN, QueryIntent

logger = logging.getLogger(__name__)

# 按渠道配置缓存 ChatOpenAI 客户端，避免每次请求重建 HTTP 连接池
_CLIENT_CACHE: dict[tuple[str, str, str, str], ChatOpenAI] = {}

SYSTEM_PROMPT = (
    "你是电商运营 Agent 的意图与槽位识别器，只做分类和参数抽取，严禁编造价格、库存或销量数据。\n"
    "按以下顺序匹配：\n"
    "1. 当用户明确确认刚才生成的方案并要求提交审批（如“确认刚才的方案”“确认提交”“提交审批”），"
    "返回 kind='confirm_plan'。注意：如果用户要求直接批准/通过/驳回审批单，仍返回 kind='unsupported'。\n"
    "2. 为单个 SKU 策划、预览或创建折扣促销（可附带查询该 SKU 的近 7 天销量或库存），返回 kind='promotion'。\n"
    "   - 若消息包含单一折扣（如 8 折、打八折、8.5 折），抽取数值到 discount（如 8、8.5）；\n"
    "   - 若消息包含单一整数持续天数（如 持续 3 天、做三天），抽取整数到 duration_days"
    "（排除“最近 7 天销量”这类查询窗口）；\n"
    "   - 若消息包含明确开始日期（如 11 月 22 日开始、2026-11-22），抽取日期文本或 YYYY-MM-DD"
    " 到 start_date；缺少开始日期仍归为 promotion；\n"
    "   - 若消息包含多组折扣/天数/日期区间、半天/小数天、具体时钟点（如上午10点）"
    "或自定义库存件数门槛，设置 ambiguous_terms=true。\n"
    "3. 仅查询单个 SKU 最近 7 天的销量或销售件数，返回 kind='sales'；不指定天数时默认 7 天。\n"
    "4. 明确要求批准/通过审批、排期执行、退款、其他销量时间范围、"
    "多 SKU 或其他任务，返回 kind='unsupported'。\n"
    "sku_code 必须在消息中原样出现；缺失时返回 null。"
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


async def classify(message: str, *, settings: AppSettings | None = None) -> QueryIntent:
    return (await classify_with_metadata(message, settings=settings)).intent


__all__ = [
    "ClassificationResult",
    "ModelUnavailable",
    "SKU_PATTERN",
    "classify",
    "classify_with_metadata",
    "clear_client_cache",
]
