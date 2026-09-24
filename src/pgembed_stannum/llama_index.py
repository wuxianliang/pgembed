"""llama-index adapter (``pgembed-stannum[llama-index]`` extra).

Importing this module is always safe; llama-index itself is imported inside
:func:`build_llama_index_retriever`. The async path offloads the synchronous
data path onto a worker thread via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from typing import Any


def build_llama_index_retriever(
    index: Any,
    *,
    similarity_top_k: int = 5,
    **search_kwargs: Any,
) -> Any:
    """Build a llama-index ``BaseRetriever`` over ``StannumIndex.search``."""
    try:
        from llama_index.core.schema import BaseRetriever, NodeWithScore, TextNode
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise ImportError(
            "StannumRetriever.for_llama_index requires the llama-index extra; "
            "install it with: pip install 'pgembed-stannum[llama-index]'"
        ) from exc

    def _hit_to_node_with_score(hit: Any) -> Any:
        return NodeWithScore(
            node=TextNode(
                text=hit.snippet or "",
                metadata={"id": hit.id, "score": hit.score, "ctid": hit.ctid},
            ),
            score=float(hit.score),
        )

    class _StannumLlamaRetriever(BaseRetriever):
        def _retrieve(self, query_bundle: Any) -> list:
            hits = index.search(
                query_bundle.query_str,
                limit=similarity_top_k,
                **search_kwargs,
            )
            return [_hit_to_node_with_score(hit) for hit in hits]

        async def aretrieve(self, query_bundle: Any) -> list:
            return await asyncio.to_thread(self._retrieve, query_bundle)

    return _StannumLlamaRetriever()
