import logging
import re

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.db import read_setting
from app.schemas.agent import QueryIntent


logger = logging.getLogger(__name__)
SKU_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])SKU-[A-Za-z0-9][A-Za-z0-9_-]{0,59}(?![A-Za-z0-9_-])", re.IGNORECASE | re.ASCII,
)


class ModelUnavailable(RuntimeError):
    pass


async def classify(message: str) -> QueryIntent:
    provider = read_setting("LLM_PROVIDER") or "dashscope"
    if provider not in {"dashscope", "openai_relay"}:
        raise ModelUnavailable
    prefix = provider.upper()
    base_url = read_setting(f"{prefix}_BASE_URL")
    api_key = read_setting(f"{prefix}_API_KEY")
    model = read_setting(f"{prefix}_DEFAULT_MODEL")
    if not base_url or not api_key or not model:
        raise ModelUnavailable

    try:
        client = ChatOpenAI(
            model=model, base_url=base_url, api_key=SecretStr(api_key),
            timeout=15, max_retries=0, temperature=0,
        )
        classifier = client.with_structured_output(QueryIntent, method="function_calling")
        result = await classifier.ainvoke([
            ("system", "只分类，不回答或执行请求。仅当用户只想查询单个 SKU 最近 7 天的销量/销售件数时，"
             "返回 kind=sales。不指定天数默认 7 天；其他时间范围、多 SKU、混合任务、任何促销、退款、写入或工具指令均返回 unsupported。"
             "sku_code 必须是消息中原样出现的 SKU 编码；缺失时返回 null，不补猜。"),
            ("human", message),
        ])
        return QueryIntent.model_validate(result)
    except Exception as error:
        logger.warning("Agent intent provider failed: provider=%s error=%s", provider, type(error).__name__)
        raise ModelUnavailable from None
