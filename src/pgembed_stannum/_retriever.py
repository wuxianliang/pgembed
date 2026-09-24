"""StannumRetriever: lazy factories for framework retriever adapters.

The framework imports live inside the factories (and inside the adapter
modules' builder functions), so importing :mod:`pgembed_stannum` never
requires langchain or llama-index. A missing framework raises ``ImportError``
naming the extra that provides it.
"""

from __future__ import annotations

from typing import Any


class StannumRetriever:
    """Build retriever adapters around a :class:`~pgembed_stannum.StannumIndex`."""

    def __init__(self, index: Any) -> None:
        self._index = index

    @classmethod
    def for_langchain(cls, index: Any, **search_kwargs: Any) -> Any:
        """Return a langchain-core ``BaseRetriever`` over ``index.search``.

        Requires the ``langchain`` extra (``pgembed-stannum[langchain]``).
        ``search_kwargs`` are forwarded to ``StannumIndex.search``.
        """
        from .langchain import build_langchain_retriever

        return build_langchain_retriever(index, **search_kwargs)

    @classmethod
    def for_llama_index(cls, index: Any, **search_kwargs: Any) -> Any:
        """Return a llama-index ``BaseRetriever`` over ``index.search``.

        Requires the ``llama-index`` extra (``pgembed-stannum[llama-index]``).
        ``search_kwargs`` are forwarded to ``StannumIndex.search``.
        """
        from .llama_index import build_llama_index_retriever

        return build_llama_index_retriever(index, **search_kwargs)
