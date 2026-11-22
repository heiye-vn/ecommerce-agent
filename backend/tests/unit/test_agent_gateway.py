import pytest

from app.agent import llm_gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dashscope", "openai_relay"])
async def test_agent_uses_only_the_selected_chat_channel(provider, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    for channel in ("DASHSCOPE", "OPENAI_RELAY"):
        monkeypatch.setenv(f"{channel}_BASE_URL", f"https://{channel.lower()}.example.invalid/v1")
        monkeypatch.setenv(f"{channel}_API_KEY", f"{channel}-fixture-key")
        monkeypatch.setenv(f"{channel}_DEFAULT_MODEL", f"{channel}-fixture-model")

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
    assert (await llm_gateway.classify(question)).model_dump() == {"kind": "sales", "sku_code": "SKU-001"}
