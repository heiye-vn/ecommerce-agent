from decimal import Decimal

import pytest

from app.agent import llm_gateway
from app.core.config import AppSettings


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
