"""langchain adapter (``pgembed-stannum[langchain]`` extra).

Importing this module is always safe; langchain itself is imported inside
:func:`build_langchain_retriever`. The async path offloads the synchronous
data path onto a worker thread via langchain's ``run_in_executor``.
"""

from __future__ import annotations

from typing import Any


def build_langchain_retriever(
    index: Any,
    *,
    k: int = 5,
    **search_kwargs: Any,
) -> Any:
    """Build a langchain-core ``BaseRetriever`` over ``StannumIndex.search``."""
    try:
        from langchain_core.callbacks import (
            AsyncCallbackManagerForLLMRun,
            CallbackManagerForLLMRun,
        )
        from langchain_core.documents import Document
        from langchain_core.retrievers import BaseRetriever
        from langchain_core.runnables.utils import run_in_executor
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise ImportError(
            "StannumRetriever.for_langchain requires the langchain extra; "
            "install it with: pip install 'pgembed-stannum[langchain]'"
        ) from exc

    def _hit_to_document(hit: Any) -> Any:
        return Document(
            page_content=hit.snippet or "",
            metadata={"id": hit.id, "score": hit.score, "ctid": hit.ctid},
        )

    class _StannumLangChainRetriever(BaseRetriever):
        index: Any
        k: int = 5
        search_kwargs: dict = {}

        def _get_relevant_documents(
            self,
            query: str,
            *,
            run_manager: CallbackManagerForLLMRun,
        ) -> list:
            hits = self.index.search(query, limit=self.k, **self.search_kwargs)
            return [_hit_to_document(hit) for hit in hits]

        async def _aget_relevant_documents(
            self,
            query: str,
            *,
            run_manager: AsyncCallbackManagerForLLMRun,
        ) -> list:
            return await run_in_executor(
                None, self._get_relevant_documents, query, run_manager=run_manager
            )

    return _StannumLangChainRetriever(index=index, k=k, search_kwargs=search_kwargs)
