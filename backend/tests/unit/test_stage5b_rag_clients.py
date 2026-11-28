import json

import httpx
import pytest

from app.agent.rag.chunker import (
    build_lexical_tsquery_expression,
    chunk_markdown_document,
    tokenize_for_lexical,
)
from app.agent.rag.embeddings import DashScopeEmbeddingClient, EmbeddingUnavailableError
from app.agent.rag.rerank import DashScopeRerankClient


def test_chunker_extracts_sections_pages_and_chinese_bigrams() -> None:
    content = (
        "# §1.1 活动定价口径\n[第 1 页]\n促销价按原价乘折扣保留两位小数。\n\n"
        "## §4.3 审批后复核\n[第 6 页]\n审批通过后，价格与审批快照不一致时不得排期。"
    )
    chunks = chunk_markdown_document(content, default_title="促销运营规则")
    assert len(chunks) == 2
    assert chunks[0].section_title == "§1.1 活动定价口径"
    assert chunks[0].page_label == "第 1 页"
    assert chunks[1].section_title == "§4.3 审批后复核"
    assert chunks[1].page_label == "第 6 页"
    assert "审批" in chunks[1].lexical_text
    assert "排期" in chunks[1].lexical_text
    assert "4.3" in chunks[1].lexical_text

    tsq = build_lexical_tsquery_expression("审批通过后，如果 SKU 的售价变了还能排期吗？")
    assert "'审批'" in tsq
    assert "'排期'" in tsq
    assert "'的'" not in tsq
    assert "tok" in tokenize_for_lexical("TOK-001 规则")


@pytest.mark.asyncio
async def test_dashscope_embedding_client_batches_and_validates_1024_dimensions() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append({"url": str(request.url), "body": body, "auth": request.headers.get("Authorization")})
        items = [
            {"index": i, "embedding": [float(i + 1) / 100.0] * 1024}
            for i in range(len(body["input"]))
        ]
        # 故意倒序返回，检验按 index 重排
        items.reverse()
        return httpx.Response(200, json={"data": items})

    client = DashScopeEmbeddingClient(
        base_url="https://dashscope.example.invalid/compatible-mode/v1",
        api_key="test-dashscope-key",
        model_name="qwen3.7-text-embedding-flash",
        dimensions=1024,
        transport=httpx.MockTransport(handler),
    )
    texts = [f"规则片段 {i}" for i in range(12)]
    vectors = await client.embed_texts(texts)

    assert len(calls) == 2
    assert len(calls[0]["body"]["input"]) == 10
    assert len(calls[1]["body"]["input"]) == 2
    assert calls[0]["body"]["model"] == "qwen3.7-text-embedding-flash"
    assert calls[0]["body"]["dimensions"] == 1024
    assert calls[0]["auth"] == "Bearer test-dashscope-key"
    assert len(vectors) == 12
    assert len(vectors[0]) == 1024
    assert vectors[0][0] == pytest.approx(0.01)

    def bad_dim_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 512}]})

    bad_client = DashScopeEmbeddingClient(
        base_url="https://dashscope.example.invalid/compatible-mode/v1",
        api_key="test-dashscope-key",
        model_name="qwen3.7-text-embedding-flash",
        dimensions=1024,
        transport=httpx.MockTransport(bad_dim_handler),
    )
    with pytest.raises(EmbeddingUnavailableError, match="期望 1024"):
        await bad_client.embed_query("测试问题")


@pytest.mark.asyncio
async def test_dashscope_rerank_client_orders_results_and_falls_back_on_timeout() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "qwen3.7-text-rerank"
        assert body["parameters"]["top_n"] == 2
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 2, "relevance_score": 0.94},
                        {"index": 0, "relevance_score": 0.61},
                    ]
                }
            },
        )

    client = DashScopeRerankClient(
        rerank_url="https://dashscope.example.invalid/rerank",
        api_key="test-dashscope-key",
        model_name="qwen3.7-text-rerank",
        transport=httpx.MockTransport(ok_handler),
    )
    outcome = await client.rerank("售价变化", ["片段0", "片段1", "片段2"], top_n=2)
    assert outcome.degraded is False
    assert [item.index for item in outcome.items] == [2, 0]
    assert outcome.items[0].score == pytest.approx(0.94)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    fallback_client = DashScopeRerankClient(
        rerank_url="https://dashscope.example.invalid/rerank",
        api_key="test-dashscope-key",
        model_name="qwen3.7-text-rerank",
        transport=httpx.MockTransport(timeout_handler),
    )
    fallback = await fallback_client.rerank("售价变化", ["片段0", "片段1", "片段2"], top_n=2)
    assert fallback.degraded is True
    assert [item.index for item in fallback.items] == [0, 1]
