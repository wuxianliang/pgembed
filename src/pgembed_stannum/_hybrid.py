"""Reciprocal-rank-fusion hybrid search: one transaction, parameters only.

The SQL form is pinned by the P0-1 plan: two rank CTEs (BM25 via
``stannum.search`` joined on ``d.ctid = s.ctid``; vector via ``<=>``),
a ``fused`` CTE that UNION ALLs both arms with ``NULL::bigint`` marking the
absent arm, and a final ``GROUP BY`` aggregation so a vector-only id reports
``bm25_rank = NULL`` and vice versa. Ranks are 1-based. The BM25 candidate
pool is ``max(limit * 5, 50)`` (5x headroom is the only recall knob for the
fused top-k).

Planner policy (plan resolution #12 — no session GUCs): read
``pg_class.reltuples`` in the same transaction; below ``seqscan_row_threshold``
the planner picks the seq scan naturally; at or above it an applicable index
on the vector column is required, else :class:`RuntimeError` (never a silent
big-table sequential scan).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Optional, Sequence

from ._index import HybridHit
from ._sql import quote_ident

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from ._index import StannumIndex

_FUSED_SQL_TEMPLATE = """
WITH bm25 AS (
    SELECT d.{id_col} AS id, s.score, s.snippet,
           row_number() OVER (ORDER BY s.score DESC, d.{id_col}) AS rank
    FROM {table} d
    JOIN stannum.search(%s::regclass, %s, "limit" => %s) AS s ON d.ctid = s.ctid),
     vec AS (
    SELECT {id_col} AS id,
           row_number() OVER (ORDER BY {vector_col} <=> %s::vector) AS rank
    FROM {table}
    ORDER BY {vector_col} <=> %s::vector LIMIT %s),
     fused AS (
    SELECT id, %s::float8 / (%s::int + rank) AS c,
           rank AS bm25_rank, NULL::bigint AS vrank, snippet
    FROM bm25
    UNION ALL
    SELECT id, %s::float8 / (%s::int + rank), NULL::bigint, rank, NULL::text
    FROM vec)
SELECT d.{id_col} AS id, sum(c) AS rrf_score,
       max(bm25_rank) AS bm25_rank, max(vrank) AS vector_rank,
       max(snippet) AS snippet
FROM fused JOIN {table} d ON d.{id_col} = fused.id
GROUP BY d.{id_col}
ORDER BY rrf_score DESC, d.{id_col}
LIMIT %s
"""

_PLANNER_SQL = """
SELECT c.reltuples::float8 AS reltuples,
       EXISTS (
           SELECT 1
           FROM pg_index i
           JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = %s
           WHERE i.indrelid = c.oid
             AND i.indisready AND i.indisvalid
             AND a.attnum = ANY (i.indkey::smallint[])
       ) AS has_vector_index
FROM pg_class c
WHERE c.oid = %s::regclass
"""


def _validate(
    limit: int,
    rrf_k: int,
    weights: Sequence[float],
    query_vector: Sequence[float],
) -> None:
    """Plan-mandated client-side validation, before any SQL runs."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be an integer >= 1, got {limit!r}")
    if not isinstance(rrf_k, int) or isinstance(rrf_k, bool) or rrf_k < 1:
        raise ValueError(f"rrf_k must be an integer >= 1, got {rrf_k!r}")
    if len(weights) != 2:
        raise ValueError(f"weights must be a (bm25, vector) pair, got {weights!r}")
    for weight in weights:
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise ValueError(f"weights must be numbers, got {weights!r}")
        if not math.isfinite(float(weight)) or weight < 0:
            raise ValueError(
                f"weights must be finite and >= 0, got {weights!r}"
            )
    if weights[0] == 0 and weights[1] == 0:
        raise ValueError("weights must not both be zero")
    if len(query_vector) == 0:
        raise ValueError("query_vector must not be empty")
    for component in query_vector:
        if not isinstance(component, (int, float)) or isinstance(component, bool):
            raise ValueError(f"query_vector components must be numbers, got {component!r}")
        if not math.isfinite(float(component)):
            raise ValueError("query_vector components must be finite")


def _vector_literal(query_vector: Sequence[float]) -> str:
    """Format the query vector as pgvector's text input form."""
    return "[" + ",".join(repr(float(v)) for v in query_vector) + "]"


def hybrid_search(
    index: "StannumIndex",
    query: str,
    *,
    vector_column: str,
    query_vector: Sequence[float],
    limit: int = 5,
    candidate_limit: Optional[int] = None,
    rrf_k: int = 60,
    weights: Sequence[float] = (0.4, 0.6),
    seqscan_row_threshold: int = 50_000,
) -> list[HybridHit]:
    """Fuse BM25 and vector ranking for ``query`` (see :class:`StannumIndex`)."""
    _validate(limit, rrf_k, weights, query_vector)

    table_ref = quote_ident(index._table)
    id_ref = quote_ident(index._id_column)
    vector_ref = quote_ident(vector_column)
    fused_sql = _FUSED_SQL_TEMPLATE.format(
        table=table_ref, id_col=id_ref, vector_col=vector_ref
    )

    pool = max(limit * 5, 50)
    vector_pool = candidate_limit if candidate_limit is not None else pool
    vector_literal = _vector_literal(query_vector)
    bm25_weight, vector_weight = float(weights[0]), float(weights[1])

    conn = index._connect()
    try:
        with conn.cursor() as cursor:
            # Planner policy and the fused query share one transaction.
            cursor.execute(
                _PLANNER_SQL, (vector_column, index._table_ref_param())
            )
            reltuples, has_vector_index = cursor.fetchone()
            if (
                reltuples is not None
                and reltuples >= seqscan_row_threshold
                and not has_vector_index
            ):
                raise RuntimeError(
                    f"table {table_ref} has ~{int(reltuples)} rows (>= "
                    f"seqscan_row_threshold={seqscan_row_threshold}) and no valid "
                    f"index covering column {vector_ref}; refusing the vector-arm "
                    f"sequential scan. Create an applicable index (e.g. hnsw or "
                    f"ivfflat on pgvector, or vchord) or raise the threshold."
                )
            cursor.execute(
                fused_sql,
                (
                    index._index_ref_param(),
                    query,
                    pool,
                    vector_literal,
                    vector_literal,
                    vector_pool,
                    bm25_weight,
                    rrf_k,
                    vector_weight,
                    rrf_k,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        conn.commit()
    finally:
        conn.close()
    return [
        HybridHit(
            id=row[0],
            score=float(row[1]),
            bm25_rank=int(row[2]) if row[2] is not None else None,
            vector_rank=int(row[3]) if row[3] is not None else None,
            snippet=row[4],
        )
        for row in rows
    ]
