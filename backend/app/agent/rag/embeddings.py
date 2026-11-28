import logging
from collections.abc import Sequence
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class DashScopeEmbeddingClient:
    """阿里百炼 OpenAI 兼容 /embeddings 客户端（默认 qwen3.7-text-embedding-flash，1024 维）。"""

    BATCH_SIZE = 10

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model_name: str,
        dimensions: int = 1024,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = (api_key or "").strip() or None
        self._model_name = model_name
        self._dimensions = dimensions
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_key:
            raise EmbeddingUnavailableError("DASHSCOPE_API_KEY 未配置，无法调用百炼向量模型。")

        endpoint = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        vectors: list[list[float]] = []

        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                for start in range(0, len(texts), self.BATCH_SIZE):
                    batch = list(texts[start : start + self.BATCH_SIZE])
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json={
                            "model": self._model_name,
                            "input": batch,
                            "dimensions": self._dimensions,
                            "encoding_format": "float",
                        },
                    )
                    if response.status_code != 200:
                        logger.warning(
                            "DashScope embedding request failed: status=%s model=%s",
                            response.status_code,
                            self._model_name,
                        )
                        raise EmbeddingUnavailableError(
                            f"百炼 Embedding 接口返回异常状态码 {response.status_code}"
                        )
                    payload = response.json()
                    raw_items = payload.get("data")
                    if not isinstance(raw_items, list) or len(raw_items) != len(batch):
                        raise EmbeddingUnavailableError("百炼 Embedding 返回条目数量与输入不匹配")
                    sorted_items = sorted(raw_items, key=lambda item: int(item.get("index", 0)))
                    for item in sorted_items:
                        embedding = item.get("embedding")
                        if not isinstance(embedding, list) or len(embedding) != self._dimensions:
                            actual = len(embedding) if isinstance(embedding, list) else -1
                            raise EmbeddingUnavailableError(
                                f"百炼 Embedding 返回维度异常：期望 {self._dimensions}，实际 {actual}"
                            )
                        vectors.append([float(val) for val in embedding])
        except EmbeddingUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning(
                "DashScope embedding transport error: model=%s error=%s",
                self._model_name,
                type(exc).__name__,
            )
            raise EmbeddingUnavailableError(f"百炼 Embedding 请求失败：{type(exc).__name__}") from None

        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_texts([text])
        return vectors[0]
