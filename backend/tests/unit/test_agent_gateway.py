from decimal import Decimal

import pytest

from app.agent import llm_gateway
from app.core.config import AppSettings
from app.schemas.knowledge import KnowledgeCitationItem


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dashscope", "openai_relay"])
async def test_agent_uses_only_the_selected_chat_channel(provider, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.delenv("LLM_STRUCTURED_OUTPUT_MODE", raising=False)
    for channel in ("DASHSCOPE", "OPENAI_RELAY"):
        monkeypatch.setenv(f"{channel}_BASE_URL", f"https://{channel.lower()}.example.invalid/v1")
        monkeypatch.setenv(f"{channel}_API_KEY", f"{channel}-fixture-key")
        monkeypatch.setenv(f"{channel}_DEFAULT_MODEL", f"{channel}-fixture-model")
        monkeypatch.delenv(f"{channel}_STRUCTURED_OUTPUT_MODE", raising=False)

    question = "查一下 SKU-001 最近 7 天销量"

    class ProviderStub:
        def __init__(self, *, model, base_url, api_key, **kwargs):
            assert model == f"{provider.upper()}-fixture-model"
            assert base_url == f"https://{provider}.example.invalid/v1"
            assert api_key.get_secret_value() == f"{provider.upper()}-fixture-key"

        def with_structured_output(self, schema, *, method):
            assert schema is llm_gateway.QueryIntent and method == "function_calling"
            return self

        async def ainvoke(self, messages):
            assert messages[1:] == [("human", question)]
            return {"kind": "sales", "sku_code": "SKU-001"}

    monkeypatch.setattr(llm_gateway, "ChatOpenAI", ProviderStub)
    intent = await llm_gateway.classify(question)
    assert intent.model_dump() == {
        "kind": "sales",
        "sku_code": "SKU-001",
        "discount": None,
        "duration_days": None,
        "start_date": None,
        "ambiguous_terms": False,
        "order_no": None,
        "refund_amount": None,
        "refund_reason": None,
        "exception_only": False,
        "target_price": None,
        "price_change_reason": None,
    }


@pytest.mark.asyncio
async def test_agent_supports_channel_structured_mode_and_metadata(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_relay")
    monkeypatch.setenv("OPENAI_RELAY_BASE_URL", "https://relay.example.invalid/v1")
    monkeypatch.setenv("OPENAI_RELAY_API_KEY", "relay-key")
    monkeypatch.setenv("OPENAI_RELAY_DEFAULT_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_RELAY_STRUCTURED_OUTPUT_MODE", "json_schema")
    llm_gateway.clear_client_cache()

    created_instances = 0

    class ProviderStub:
        def __init__(self, *, model, base_url, api_key, **kwargs):
            nonlocal created_instances
            created_instances += 1
            assert model == "gpt-4.1-mini"

        def with_structured_output(self, schema, *, method):
            assert schema is llm_gateway.QueryIntent and method == "json_schema"
            return self

        async def ainvoke(self, messages):
            return {
                "kind": "promotion",
                "sku_code": "SKU-001",
                "discount": "8",
                "duration_days": 3,
                "start_date": "2026-11-22",
            }

    monkeypatch.setattr(llm_gateway, "ChatOpenAI", ProviderStub)
    settings = AppSettings.load()
    first = await llm_gateway.classify_with_metadata("给 SKU-001 打八折做三天", settings=settings)
    second = await llm_gateway.classify_with_metadata("给 SKU-001 打八折做三天", settings=settings)

    assert created_instances == 1
    assert first.llm_provider == "openai_relay"
    assert first.model_name == "gpt-4.1-mini"
    assert first.intent.discount == Decimal("8")
    assert first.intent.duration_days == 3
    assert second.intent.start_date == "2026-11-22"
    llm_gateway.clear_client_cache()


@pytest.mark.asyncio
async def test_gateway_supports_knowledge_qa_intent_and_answer_synthesis(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.example.invalid/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_DEFAULT_MODEL", "qwen-plus")
    llm_gateway.clear_client_cache()

    class ProviderStub:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, _schema, *, method):
            assert method == "function_calling"
            return self

        async def ainvoke(self, messages):
            if len(messages) == 2 and "问题：" in messages[1][1]:
                return type("Msg", (), {"content": "不能按原方案排期，需重新提交审批。"})()
            return {"kind": "knowledge_qa"}

    monkeypatch.setattr(llm_gateway, "ChatOpenAI", ProviderStub)
    settings = AppSettings.load()
    result = await llm_gateway.classify_with_metadata(
        "审批通过后如果 SKU 售价变了，还能按原方案排期吗？",
        settings=settings,
    )
    assert result.intent.kind == "knowledge_qa"

    citation = KnowledgeCitationItem(
        document_id=1,
        doc_no="DOC-001",
        title="促销运营规则",
        document_version=3,
        chunk_id=2,
        chunk_index=1,
        section_title="§4.3 审批后复核",
        page_label="第 6 页",
        visibility_scope="org_operator",
        visibility_label="组织运营",
        excerpt="价格与审批快照不一致时不得排期。",
        content="价格与审批快照不一致时不得排期。",
        rrf_score=0.032,
    )
    synthesized = await llm_gateway.synthesize_knowledge_answer(
        "审批通过后如果 SKU 售价变了，还能按原方案排期吗？",
        [citation],
        settings=settings,
    )
    assert "不能按原方案排期" in synthesized

    monkeypatch.setenv("LLM_PROVIDER", "unsupported_channel")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("OPENAI_RELAY_API_KEY", "")
    llm_gateway.clear_client_cache()
    fallback = await llm_gateway.synthesize_knowledge_answer(
        "价格变了怎么办？",
        [citation],
        settings=AppSettings.load(),
    )
    assert "DOC-001" in fallback
    assert "§4.3 审批后复核" in fallback
    llm_gateway.clear_client_cache()
