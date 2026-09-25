"""StannumIndex: the pgembed_stannum data path.

All data access goes through psycopg2 with ``%s`` parameters — never
``psql()`` string interpolation — because agent queries contain quotes.
Identifiers are quoted via :mod:`pgembed_stannum._sql`.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ._sql import quote_ident, split_qualified, truncate_identifier

#: Tokenizer values accepted by the stannum access method's ``tokenizer``
#: reloption (and by ``stannum.tokenize``).
TOKENIZERS = ("unicode", "whitespace", "jieba")

#: Snippet modes accepted by ``stannum.search``.
SNIPPET_MODES = ("none", "html", "ansi")

#: Characters that keep their special meaning inside a quoted TINQL phrase
#: and therefore must be backslash-escaped when re-emitting tokens.
_PHRASE_ESCAPE_CHARS = ('\\', '"', "[", "]", "_")


@dataclass(frozen=True)
class SearchHit:
    """One ranked row from :meth:`StannumIndex.search`.

    ``id`` is the value of the index's ``id_column``; ``ctid`` is the visible
    HOT-member ctid text (``"(block,offset)"``) the SRF joined on.
    """

    id: Any
    ctid: str
    score: float
    snippet: Optional[str]


@dataclass(frozen=True)
class HybridHit:
    """One reciprocal-rank-fused row from :meth:`StannumIndex.hybrid_search`.

    ``score`` is the fused RRF score; ``bm25_rank``/``vector_rank`` are 1-based
    ranks within their arm and ``None`` when the row was absent from that arm.
    ``snippet`` is the BM25 arm's snippet (``None`` for vector-only rows).
    """

    id: Any
    score: float
    bm25_rank: Optional[int]
    vector_rank: Optional[int]
    snippet: Optional[str]


@dataclass(frozen=True)
class IndexAnalysis:
    """One row of ``stannum.index_analysis`` (dictionary drift status)."""

    index_name: str
    recorded_jieba_version: Optional[int]
    recorded_dict_fingerprint: Optional[int]
    runtime_jieba_version: Optional[int]
    runtime_dict_fingerprint: Optional[int]
    matches: Optional[bool]
    status: str


def _quote_tinql_phrase_token(token: str) -> str:
    """Quote one analyzed token as a literal TINQL exact term."""
    escaped = "".join("\\" + ch if ch in _PHRASE_ESCAPE_CHARS else ch for ch in token)
    return f'"{escaped}"'


def _format_field_weights(field_weights: dict[str, float]) -> str:
    """Validate a field_weights mapping and render the reloption's value.

    Mirrors the SQL side's rules so bad input fails before any connection:
    at least two columns, column names free of the ``,`` and ``:``
    separators, and weights that parse back as finite positive f32 values
    (the server reads each entry with ``f32::from_str``). Entries are
    emitted in mapping order as ``name:weight``.
    """
    if not isinstance(field_weights, dict):
        raise ValueError(
            "field_weights must be a dict of column name -> weight, got"
            f" {type(field_weights).__name__}"
        )
    if len(field_weights) < 2:
        raise ValueError(
            "field_weights needs at least two columns; stannum single-column"
            " indexes take no weights"
        )
    if len(field_weights) > 16:
        raise ValueError(
            f"field_weights supports at most 16 columns, got {len(field_weights)}"
        )
    entries: list[str] = []
    for name, weight in field_weights.items():
        if not isinstance(name, str) or not name or "," in name or ":" in name:
            raise ValueError(
                "field_weights column names must be non-empty strings without"
                f" ',' or ':', got {name!r}"
            )
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError(
                f"field_weights[{name!r}] must be a number, got {weight!r}"
            )
        try:
            value = float(weight)
            as_f32 = struct.unpack("f", struct.pack("f", value))[0]
        except (OverflowError, struct.error):
            raise ValueError(
                f"field_weights[{name!r}] must be a finite positive number,"
                f" got {weight!r}"
            ) from None
        if not math.isfinite(as_f32) or as_f32 <= 0.0:
            raise ValueError(
                f"field_weights[{name!r}] must be a finite positive number,"
                f" got {weight!r}"
            )
        entries.append(f"{name}:{value!r}")
    return ",".join(entries)


class StannumIndex:
    """Manage one stannum BM25 index on one or more table columns and search it.

    Parameters mirror the plan's P0-1 API: ``table`` may be schema-qualified
    (1-2 parts), ``tokenizer`` is the index's ``tokenizer`` reloption,
    ``id_column`` is the column returned by :meth:`search` /
    :meth:`hybrid_search`, and ``index_name`` overrides the default name
    ``{table}_{column}_stannum_idx`` (truncated to the identifier limit with a
    deterministic hash suffix when too long). Pass ``field_weights`` to
    :meth:`create` for a multi-column BM25F index (stannum 0.4.0).
    """

    def __init__(
        self,
        server: Any,
        table: str,
        column: str,
        *,
        tokenizer: str = "jieba",
        id_column: str = "id",
        index_name: Optional[str] = None,
    ) -> None:
        if tokenizer not in TOKENIZERS:
            raise ValueError(
                f"tokenizer must be one of {TOKENIZERS}, got {tokenizer!r}"
            )
        self._server = server
        self._schema, self._table_name = split_qualified(table)
        self._table = table
        self._column = column
        self._tokenizer = tokenizer
        self._id_column = id_column
        self._index_name = index_name or truncate_identifier(
            f"{self._table_name}_{self._column}_stannum_idx"
        )
        # split_qualified already validated the parts; index_name gets the
        # same treatment when interpolated.
        self._index_ref = quote_ident(
            f"{self._schema}.{self._index_name}" if self._schema else self._index_name
        )

    @property
    def index_name(self) -> str:
        return self._index_name

    # -- connection helper -------------------------------------------------

    def _connect(self):
        import psycopg2

        return psycopg2.connect(self._server.get_uri())

    def _table_ref(self) -> str:
        return quote_ident(self._table)

    def _column_ref(self) -> str:
        return quote_ident(self._column)

    def _id_ref(self) -> str:
        return quote_ident(self._id_column)

    # -- DDL ----------------------------------------------------------------

    def create(self, *, field_weights: Optional[dict[str, float]] = None) -> None:
        """Create the extension (if needed) and the stannum index.

        Idempotent: both statements use ``IF NOT EXISTS``.

        ``field_weights`` creates a multi-column BM25F index (stannum 0.4.0):
        the mapping's keys are the index's key columns in DDL order and its
        values are their finite positive weights (stannum requires the
        weights to cover every key column, which the keys-as-columns form
        guarantees). The mapping must name at least two columns, and column
        names containing ``,`` or ``:`` cannot be spelled in the reloption
        and are rejected. Without ``field_weights`` the index stays
        single-column on ``column``.
        """
        columns = self._column_ref()
        options = f"tokenizer = '{self._tokenizer}'"
        if field_weights is not None:
            weights = _format_field_weights(field_weights)
            columns = ", ".join(quote_ident(name) for name in field_weights)
            # The utility statement takes no bind parameters, so the weights
            # ride as an escaped string literal (single quotes doubled).
            options += ", field_weights = '" + weights.replace("'", "''") + "'"
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS stannum")
                cursor.execute(
                    # CREATE INDEX takes an unqualified name: PostgreSQL always
                    # creates the index in the ON-table's schema. The qualified
                    # reference stays correct for DROP INDEX and regclass casts.
                    f"CREATE INDEX IF NOT EXISTS {quote_ident(self._index_name)}"
                    f" ON {self._table_ref()} USING stannum ({columns})"
                    f" WITH ({options})"
                )
            conn.commit()
        finally:
            conn.close()

    def drop(self) -> None:
        """Drop the index (idempotent)."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP INDEX IF EXISTS {self._index_ref}")
            conn.commit()
        finally:
            conn.close()

    # -- search -------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        snippet: str = "html",
        drop_stop_words: bool = False,
        begin_tag: Optional[str] = None,
        end_tag: Optional[str] = None,
    ) -> list[SearchHit]:
        """Run one parameterized ``stannum.search`` joined on ``d.ctid = s.ctid``.

        With ``drop_stop_words=True`` the query is treated as plain text (not
        TINQL): it is tokenized with the index's tokenizer via
        ``stannum.tokenize``, preset stop words (``stannum.builtin_stop_words
        ('auto')``, loaded from the SRF at runtime) are dropped, and the
        remaining tokens are re-joined quoted as exact terms with implicit
        AND. If nothing remains, there are no hits.

        ``begin_tag``/``end_tag`` override the snippet's highlight tags
        (SRF defaults ``<mark>``/``</mark>``); when ``None`` (the default) the
        parameters are omitted entirely so the SRF defaults — and the
        generated SQL — are unchanged for existing callers. They only affect
        snippet rendering in ``html`` mode; ``ansi`` mode ignores them and
        renders escape sequences (matching the SRF).
        """
        if drop_stop_words:
            query = self._strip_stop_words(query)
            if not query:
                return []
        srf_args = '"limit" => %s, "snippet" => %s'
        params: tuple[Any, ...] = (self._index_ref_param(), query, limit, snippet)
        if begin_tag is not None:
            srf_args += ', "begin_tag" => %s'
            params += (begin_tag,)
        if end_tag is not None:
            srf_args += ', "end_tag" => %s'
            params += (end_tag,)
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT d.{self._id_ref()} AS id, s.ctid::text AS ctid,"
                    f" s.score::float8 AS score, s.snippet"
                    f" FROM {self._table_ref()} d"
                    f" JOIN stannum.search(%s::regclass, %s, {srf_args}) AS s"
                    f" ON d.ctid = s.ctid"
                    f" ORDER BY s.score DESC, d.{self._id_ref()}",
                    params,
                )
                rows = cursor.fetchall()
        finally:
            conn.close()
        return [
            SearchHit(id=row[0], ctid=row[1], score=float(row[2]), snippet=row[3])
            for row in rows
        ]

    def search_count(self, query: str) -> int:
        """Return the visible match count via ``stannum.search_count``."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT stannum.search_count(%s::regclass, %s)",
                    (self._index_ref_param(), query),
                )
                row = cursor.fetchone()
        finally:
            conn.close()
        return int(row[0])

    def analysis(self) -> IndexAnalysis:
        """Return the index's dictionary-drift analysis (one row)."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT index_name, recorded_jieba_version,"
                    " recorded_dict_fingerprint, runtime_jieba_version,"
                    " runtime_dict_fingerprint, matches, status"
                    " FROM stannum.index_analysis(%s::regclass)",
                    (self._index_ref_param(),),
                )
                row = cursor.fetchone()
        finally:
            conn.close()
        if row is None:
            raise RuntimeError(
                f"stannum.index_analysis({self._index_name!r}) returned no rows"
            )
        return IndexAnalysis(
            index_name=row[0],
            recorded_jieba_version=row[1],
            recorded_dict_fingerprint=row[2],
            runtime_jieba_version=row[3],
            runtime_dict_fingerprint=row[4],
            matches=row[5],
            status=row[6],
        )

    def check_health(self) -> list[dict[str, str]]:
        """Run ``stannum.verify_index``; an empty list means the index is consistent."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT severity, location, message"
                    " FROM stannum.verify_index(%s::regclass, heap_check => false)",
                    (self._index_ref_param(),),
                )
                rows = cursor.fetchall()
        finally:
            conn.close()
        return [
            {"severity": row[0], "location": row[1], "message": row[2]} for row in rows
        ]

    def hybrid_search(
        self,
        query: str,
        *,
        vector_column: str,
        query_vector: Sequence[float],
        limit: int = 5,
        candidate_limit: Optional[int] = None,
        rrf_k: int = 60,
        weights: tuple[float, float] = (0.4, 0.6),
        seqscan_row_threshold: int = 50_000,
    ) -> list[HybridHit]:
        """Reciprocal-rank-fuse BM25 and vector search (see :mod:`._hybrid`)."""
        from ._hybrid import hybrid_search

        return hybrid_search(
            self,
            query,
            vector_column=vector_column,
            query_vector=query_vector,
            limit=limit,
            candidate_limit=candidate_limit,
            rrf_k=rrf_k,
            weights=weights,
            seqscan_row_threshold=seqscan_row_threshold,
        )

    # -- stop-word stripping --------------------------------------------------

    def _index_ref_param(self) -> str:
        """The index reference as a text parameter for ``::regclass`` casts."""
        return f"{self._schema}.{self._index_name}" if self._schema else self._index_name

    def _table_ref_param(self) -> str:
        """The table reference as a text parameter for ``::regclass`` casts."""
        return self._table

    def _strip_stop_words(self, query: str) -> str:
        """Plain-text mode: tokenize, drop preset stop words, re-join as exact terms."""
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT tok FROM stannum.builtin_stop_words('auto') AS t(tok)"
                )
                stop_words = {row[0] for row in cursor.fetchall()}
                cursor.execute(
                    "SELECT tok FROM stannum.tokenize(%s, tokenizer => %s) AS t(tok)",
                    (query, self._tokenizer),
                )
                tokens = [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()
        kept = [token for token in tokens if token not in stop_words]
        return " ".join(_quote_tinql_phrase_token(token) for token in kept)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"StannumIndex(table={self._table!r}, column={self._column!r},"
            f" index={self._index_name!r}, tokenizer={self._tokenizer!r})"
        )
