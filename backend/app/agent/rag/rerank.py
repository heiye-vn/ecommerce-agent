import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankResultItem:
    index: int
    score: float


@dataclass(frozen=True)
class RerankOutcome:
    items: list[RerankResultItem]
    model_name: str | None
    degraded: bool
    error: str | None = None


class RerankClient(Protocol):
    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int,
    ) -> RerankOutcome: ...


class DashScopeRerankClient:
    """阿里百炼 text-rerank 客户端（失败或超时自动安全退化为 RRF 顺序）。"""

    def __init__(
        self,
        *,
        rerank_url: str,
        api_key: str | None,
        model_name: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = rerank_url.strip()
        self._api_key = (api_key or "").strip() or None
        self._model_name = model_name
        self._timeout = timeout_seconds
        self._transport = transport

    @staticmethod
    def _fallback_rrf_order(total: int, top_n: int, model_name: str, reason: str) -> RerankOutcome:
        limit = min(max(top_n, 0), total)
        return RerankOutcome(
            items=[RerankResultItem(index=idx, score=0.0) for idx in range(limit)],
            model_name=model_name,
            degraded=True,
            error=reason,
        )

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int,
    ) -> RerankOutcome:
        total = len(documents)
        if total == 0 or top_n <= 0:
            return RerankOutcome(items=[], model_name=self._model_name, degraded=False)
        if not self._api_key or not self._url:
            return self._fallback_rrf_order(total, top_n, self._model_name, "missing_rerank_credentials")

        effective_top_n = min(top_n, total)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model_name,
                        "input": {
                            "query": query,
                            "documents": list(documents),
                        },
                        "parameters": {
                            "top_n": effective_top_n,
                            "return_documents": False,
                        },
                    },
                )
            if response.status_code != 200:
                logger.warning(
                    "DashScope rerank failed and fell back to RRF: status=%s model=%s",
                    response.status_code,
                    self._model_name,
                )
                return self._fallback_rrf_order(
                    total, effective_top_n, self._model_name, f"http_{response.status_code}"
                )

            payload = response.json()
            raw_results = (
                payload.get("output", {}).get("results")
                if isinstance(payload.get("output"), dict)
                else payload.get("results")
            )
            if not isinstance(raw_results, list) or not raw_results:
                return self._fallback_rrf_order(total, effective_top_n, self._model_name, "empty_results")

            parsed: list[RerankResultItem] = []
            seen_indices: set[int] = set()
            for entry in raw_results:
                if not isinstance(entry, dict) or "index" not in entry:
                    continue
                idx = int(entry["index"])
                if not (0 <= idx < total) or idx in seen_indices:
                    continue
                seen_indices.add(idx)
                raw_score = entry.get("relevance_score", entry.get("score"))
                score = float(raw_score) if raw_score is not None else 0.0
                parsed.append(RerankResultItem(index=idx, score=score))

            if not parsed:
                return self._fallback_rrf_order(total, effective_top_n, self._model_name, "invalid_results")

            parsed.sort(key=lambda item: item.score, reverse=True)
            return RerankOutcome(
                items=parsed[:effective_top_n],
                model_name=self._model_name,
                degraded=False,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "DashScope rerank error and fell back to RRF: model=%s error=%s",
                self._model_name,
                type(exc).__name__,
            )
            return self._fallback_rrf_order(total, effective_top_n, self._model_name, type(exc).__name__)
