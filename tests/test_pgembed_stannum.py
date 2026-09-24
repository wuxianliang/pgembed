"""pgembed_stannum package tests: StannumIndex data path, RRF hybrid, retrievers.

The data path must go through psycopg2 ``%s`` parameters (never ``psql()``
string interpolation): agent queries contain quotes, and ``psql()`` runs with
``ON_ERROR_STOP`` which raises ``CalledProcessError`` on the first
quote-containing query. The hybrid test hand-computes reciprocal-rank fusion
from the two arms observed independently, so it pins the fusion math without
hard-coding BM25 scores.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import tempfile
from typing import Iterator

import pytest

import pgembed
from pgembed_stannum import (
    IndexAnalysis,
    SearchHit,
    StannumIndex,
    StannumRetriever,
)
from pgembed_stannum._sql import (
    quote_ident,
    quote_part,
    split_qualified,
    truncate_identifier,
)

DOCS_SQL = """
CREATE TABLE docs (id int PRIMARY KEY, body text, embedding vector(3));
INSERT INTO docs VALUES
 (1, 'PostgreSQL database kernel internals', '[0.9, 0.5, 0.1]'),
 (2, 'The database index and query planner', '[0.1, 0.0, 0.0]'),
 (3, 'Storage engine write path', '[0.11, 0.01, 0.0]');
"""

# 的 and 数据库 are separate jieba words; the spaces pin the segmentation.
ZH_DOCS_SQL = """
CREATE TABLE zh_docs (id int PRIMARY KEY, body text);
INSERT INTO zh_docs VALUES
 (1, '系统 的 重要'),
 (2, '数据库 系统');
"""


@pytest.fixture
def stannum_server() -> Iterator[pgembed.PostgresServer]:
    if not pgembed.has_extension("stannum"):
        pytest.skip("stannum is not installed in this build")
    tmpdir = tempfile.mkdtemp()
    with pgembed.get_server(tmpdir, cleanup_mode="delete") as pg:
        yield pg


@pytest.fixture
def docs_index(stannum_server: pgembed.PostgresServer) -> StannumIndex:
    stannum_server.psql("CREATE EXTENSION IF NOT EXISTS vector;")
    stannum_server.psql(DOCS_SQL)
    index = StannumIndex(stannum_server, "docs", "body")
    index.create()
    return index


def scalar(pg: pgembed.PostgresServer, sql: str) -> str:
    """Single value of a one-row psql result (headers stripped)."""
    return pg.psql(sql).splitlines()[2].strip()


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        # find_spec on a dotted name raises when the parent is missing.
        return False


# -- create / drop ----------------------------------------------------------


def test_create_names_index_and_is_idempotent(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    pg.psql("CREATE TABLE docs (id int PRIMARY KEY, body text);")
    index = StannumIndex(pg, "docs", "body")
    assert index.index_name == "docs_body_stannum_idx"
    index.create()
    index.create()  # IF NOT EXISTS keeps it idempotent
    assert scalar(
        pg,
        "SELECT count(*) FROM pg_class WHERE relname = 'docs_body_stannum_idx'"
        " AND relkind = 'i';",
    ) == "1"
    assert scalar(
        pg,
        "SELECT a.amname FROM pg_class c JOIN pg_am a ON a.oid = c.relam"
        " WHERE c.relname = 'docs_body_stannum_idx';",
    ) == "stannum"


def test_default_index_name_uses_unqualified_table(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    pg.psql("CREATE SCHEMA extra; CREATE TABLE extra.docs (id int, body text);")
    index = StannumIndex(pg, "extra.docs", "body")
    assert index.index_name == "docs_body_stannum_idx"


def test_long_default_index_name_truncates_with_hash_suffix(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    long_table = "t" * 52
    pg.psql(f'CREATE TABLE "{long_table}" (id int PRIMARY KEY, body text);')
    index = StannumIndex(pg, long_table, "body")
    name = index.index_name
    assert len(name.encode()) == 63
    assert name.startswith(long_table)
    assert re.fullmatch(r"[0-9a-f]{8}", name.rsplit("_", 1)[1])
    assert StannumIndex(pg, long_table, "body").index_name == name
    index.create()
    assert scalar(
        pg, f"SELECT count(*) FROM pg_class WHERE relname = '{name}';"
    ) == "1"


def test_drop_is_idempotent(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    docs_index.drop()
    docs_index.drop()
    assert scalar(
        pg, "SELECT count(*) FROM pg_class WHERE relname = 'docs_body_stannum_idx';"
    ) == "0"


def test_field_weights_rejected_until_lsg4(docs_index: StannumIndex) -> None:
    with pytest.raises(NotImplementedError, match="LSG4"):
        docs_index.create(field_weights={"title": 2.0})


# -- search / count ---------------------------------------------------------


def test_search_returns_ranked_hits_with_snippets(docs_index: StannumIndex) -> None:
    hits = docs_index.search("database", limit=5)
    assert {hit.id for hit in hits} == {1, 2}
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    for hit in hits:
        assert isinstance(hit, SearchHit)
        assert hit.score > 0.0
        assert re.fullmatch(r"\(\d+,\d+\)", hit.ctid)
        assert "<mark>database</mark>" in hit.snippet


def test_search_snippet_none_returns_null_snippet(docs_index: StannumIndex) -> None:
    hits = docs_index.search("database", limit=5, snippet="none")
    assert hits
    assert all(hit.snippet is None for hit in hits)


def test_search_with_quotes_returns_normally(docs_index: StannumIndex) -> None:
    # The apostrophes reach PostgreSQL as psycopg2 parameters, so the query
    # returns normally instead of raising CalledProcessError like psql() would.
    hits = docs_index.search("it's a 'database' --; DROP TABLE docs;")
    assert isinstance(hits, list)
    assert docs_index.search_count("O'Brien's database") == 0


def test_search_count_matches_visible_documents(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    assert docs_index.search_count("database") == 2
    assert docs_index.search_count("kernel") == 1
    assert docs_index.search_count("nonexistentterm") == 0
    assert scalar(pg, "SELECT count(*) FROM docs WHERE body ==> 'database';") == "2"


# -- analysis / health --------------------------------------------------------


def test_analysis_reports_dictionary_status(docs_index: StannumIndex) -> None:
    analysis = docs_index.analysis()
    assert isinstance(analysis, IndexAnalysis)
    assert analysis.index_name == "docs_body_stannum_idx"
    assert analysis.matches is True
    assert analysis.status == "matches"
    assert analysis.runtime_jieba_version is not None


def test_check_health_clean_on_fresh_index(docs_index: StannumIndex) -> None:
    assert docs_index.check_health() == []


# -- drop_stop_words ----------------------------------------------------------


def test_drop_stop_words_removes_preset_terms(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    pg.psql(ZH_DOCS_SQL)
    index = StannumIndex(pg, "zh_docs", "body")
    index.create()
    # Premises: both preset words really are in the 'auto' SRF output.
    assert scalar(
        pg,
        "SELECT count(*) FROM stannum.builtin_stop_words('auto') AS t(tok)"
        " WHERE t.tok = '的';",
    ) == "1"
    assert scalar(
        pg,
        "SELECT count(*) FROM stannum.builtin_stop_words('auto') AS t(tok)"
        " WHERE t.tok = 'the';",
    ) == "1"
    # TINQL mode: 的 AND 数据库 matches nothing (no doc has both words).
    assert index.search("的 数据库") == []
    # Plain-text mode: 的 is dropped, so the query reduces to 数据库 -> doc 2.
    hits = index.search("的 数据库", drop_stop_words=True)
    assert [hit.id for hit in hits] == [2]
    # Every token dropped -> no hits, and no SQL error from an empty query.
    assert index.search("的", drop_stop_words=True) == []


def test_drop_stop_words_english_terms(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    # TINQL mode: the AND database matches only doc 2 ("The database ...").
    assert [hit.id for hit in docs_index.search("the database")] == [2]
    # Plain-text mode: 'the' is dropped, leaving database -> docs 1 and 2.
    hits = docs_index.search("the database", drop_stop_words=True)
    assert {hit.id for hit in hits} == {1, 2}


# -- hybrid RRF ---------------------------------------------------------------


def test_hybrid_rrf_hand_computed(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    pg.psql("ANALYZE docs;")
    rrf_k, bm25_weight, vector_weight = 1, 0.5, 0.5

    bm25_hits = docs_index.search("database", limit=10, snippet="none")
    bm25_rank = {hit.id: rank for rank, hit in enumerate(bm25_hits, start=1)}
    assert set(bm25_rank) == {1, 2}
    vec_ids = [
        int(line.strip())
        for line in pg.psql(
            "SELECT id FROM docs ORDER BY embedding <=> '[0.1,0,0]'::vector;"
        ).splitlines()[2:]
        if re.fullmatch(r"\d+", line.strip())
    ]
    vec_rank = {doc_id: rank for rank, doc_id in enumerate(vec_ids, start=1)}
    assert set(vec_rank) == {1, 2, 3}

    expected: dict[int, float] = {}
    for doc_id in (1, 2, 3):
        score = 0.0
        if doc_id in bm25_rank:
            score += bm25_weight / (rrf_k + bm25_rank[doc_id])
        if doc_id in vec_rank:
            score += vector_weight / (rrf_k + vec_rank[doc_id])
        expected[doc_id] = score

    hits = docs_index.hybrid_search(
        "database",
        vector_column="embedding",
        query_vector=[0.1, 0.0, 0.0],
        limit=3,
        rrf_k=rrf_k,
        weights=(bm25_weight, vector_weight),
    )
    assert [hit.id for hit in hits] == sorted(
        expected, key=lambda doc_id: (-expected[doc_id], doc_id)
    )
    for hit in hits:
        assert hit.score == pytest.approx(expected[hit.id], abs=1e-9)
        assert hit.bm25_rank == bm25_rank.get(hit.id)
        assert hit.vector_rank == vec_rank.get(hit.id)
    vector_only = next(hit for hit in hits if hit.bm25_rank is None)
    assert vector_only.id == 3  # never in the BM25 arm
    assert vector_only.snippet is None
    for hit in hits:
        if hit.bm25_rank is not None:
            assert "<mark>database</mark>" in hit.snippet


def test_hybrid_planner_policy_requires_index_at_threshold(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    pg.psql("ANALYZE docs;")  # reltuples ~3, above a zero threshold
    kwargs = dict(
        vector_column="embedding", query_vector=[0.1, 0.0, 0.0], limit=2
    )
    with pytest.raises(RuntimeError, match="seqscan_row_threshold"):
        docs_index.hybrid_search("database", seqscan_row_threshold=0, **kwargs)
    # Default threshold leaves the planner free on a 3-row table.
    assert isinstance(
        docs_index.hybrid_search("database", **kwargs), list
    )
    # An applicable index on the vector column satisfies the policy.
    pg.psql(
        "CREATE INDEX docs_embedding_hnsw ON docs"
        " USING hnsw (embedding vector_cosine_ops);"
    )
    hits = docs_index.hybrid_search("database", seqscan_row_threshold=0, **kwargs)
    assert len(hits) == 2  # limit=2
    assert {hit.id for hit in hits} <= {1, 2, 3}


def test_hybrid_validates_arguments_before_sql() -> None:
    index = StannumIndex(None, "docs", "body")  # validation runs before any connect
    kwargs = dict(vector_column="embedding", query_vector=[0.1, 0.0])
    with pytest.raises(ValueError):
        index.hybrid_search("q", limit=0, **kwargs)
    with pytest.raises(ValueError):
        index.hybrid_search("q", rrf_k=0, **kwargs)
    with pytest.raises(ValueError):
        index.hybrid_search("q", weights=(0, 0), **kwargs)
    with pytest.raises(ValueError):
        index.hybrid_search("q", weights=(-1.0, 1.0), **kwargs)
    with pytest.raises(ValueError):
        index.hybrid_search("q", weights=(float("nan"), 1.0), **kwargs)
    with pytest.raises(ValueError):
        index.hybrid_search("q", vector_column="embedding", query_vector=[])
    with pytest.raises(ValueError):
        index.hybrid_search(
            "q", vector_column="embedding", query_vector=[float("inf")]
        )


# -- retriever extras (smoke; behind importorskip) ------------------------------


def test_langchain_retriever_smoke(docs_index: StannumIndex) -> None:
    pytest.importorskip(
        "langchain_core", reason="pgembed-stannum[langchain] extra not installed"
    )
    retriever = StannumRetriever.for_langchain(docs_index, k=2)
    documents = retriever.invoke("database")
    assert 0 < len(documents) <= 2
    for document in documents:
        assert isinstance(document.page_content, str)
        assert {"id", "score", "ctid"} <= set(document.metadata)
        assert isinstance(document.metadata["score"], float)
    async_documents = asyncio.run(retriever.ainvoke("database"))
    assert [d.metadata["id"] for d in async_documents] == [
        d.metadata["id"] for d in documents
    ]


def test_langchain_retriever_missing_extra_names_the_extra(
    docs_index: StannumIndex,
) -> None:
    if _has_module("langchain_core"):
        pytest.skip("langchain_core is installed; the extra is present")
    with pytest.raises(ImportError, match="langchain"):
        StannumRetriever.for_langchain(docs_index)


def test_llama_index_retriever_smoke(docs_index: StannumIndex) -> None:
    pytest.importorskip(
        "llama_index.core", reason="pgembed-stannum[llama-index] extra not installed"
    )
    retriever = StannumRetriever.for_llama_index(docs_index, similarity_top_k=2)
    nodes = retriever.retrieve("database")
    assert 0 < len(nodes) <= 2
    for node in nodes:
        assert node.score > 0.0
        assert isinstance(node.get_content(), str)
        assert {"id", "score", "ctid"} <= set(node.metadata)
    async_nodes = asyncio.run(retriever.aretrieve("database"))
    assert [n.node_id for n in async_nodes] == [n.node_id for n in nodes]


def test_llama_index_retriever_missing_extra_names_the_extra(
    docs_index: StannumIndex,
) -> None:
    if _has_module("llama_index.core"):
        pytest.skip("llama_index.core is installed; the extra is present")
    with pytest.raises(ImportError, match="llama-index"):
        StannumRetriever.for_llama_index(docs_index)


# -- identifier quoting (_sql, pure unit) ---------------------------------------


def test_quote_ident_quotes_simple_schema_qualified_and_embedded_quotes() -> None:
    assert quote_ident("docs") == '"docs"'
    assert quote_ident("public.docs") == '"public"."docs"'
    # Embedded double quotes are doubled, never terminating the identifier.
    assert quote_ident('we"ird') == '"we""ird"'
    assert quote_ident('sch"ema.ta"ble') == '"sch""ema"."ta""ble"'


def test_split_qualified_accepts_one_and_two_parts() -> None:
    assert split_qualified("docs") == (None, "docs")
    assert split_qualified("public.docs") == ("public", "docs")


@pytest.mark.parametrize("identifier", ["db.public.docs", "a.b.c.d"])
def test_split_qualified_rejects_more_than_two_parts(identifier: str) -> None:
    with pytest.raises(ValueError, match="more than two"):
        split_qualified(identifier)


@pytest.mark.parametrize("identifier", ["", "public.", ".docs"])
def test_split_qualified_rejects_empty_parts(identifier: str) -> None:
    with pytest.raises(ValueError, match="empty part"):
        split_qualified(identifier)


def test_split_qualified_rejects_nul_bytes_and_non_strings() -> None:
    with pytest.raises(ValueError, match="NUL"):
        split_qualified("doc\x00s")
    with pytest.raises(ValueError, match="NUL"):
        split_qualified("pub\x00lic.docs")
    with pytest.raises(ValueError, match="must be a str"):
        split_qualified(b"docs")


def test_quote_part_rejects_empty_and_nul() -> None:
    with pytest.raises(ValueError, match="empty"):
        quote_part("")
    with pytest.raises(ValueError, match="NUL"):
        quote_part("a\x00b")


def test_truncate_identifier_passes_short_names_through() -> None:
    assert truncate_identifier("short") == "short"
    assert truncate_identifier("i" * 63) == "i" * 63  # exactly at the limit


def test_truncate_identifier_appends_deterministic_hash_suffix() -> None:
    truncated = truncate_identifier("i" * 64)
    prefix, digest = truncated.rsplit("_", 1)
    assert prefix == "i" * (63 - 9)  # 54 readable bytes + "_" + 8 hex = 63
    assert re.fullmatch(r"[0-9a-f]{8}", digest)
    assert len(truncated.encode()) == 63
    assert truncate_identifier("i" * 64) == truncated  # same input, same output


def test_truncate_identifier_never_splits_a_utf8_sequence() -> None:
    # 53 ASCII bytes + 3-byte characters: byte 54 would land inside the first
    # multibyte character, so the readable prefix backs off to 53 bytes.
    base = "t" * 53 + "€" * 10
    truncated = truncate_identifier(base)
    encoded = truncated.encode()
    assert len(encoded) <= 63
    assert truncated[:53] == "t" * 53
    assert encoded[53] == ord("_")  # no partial UTF-8 sequence before the suffix
    # A 63-byte multibyte name is still within the limit and passes through.
    exact = "t" * 54 + "€" * 3
    assert len(exact.encode()) == 63
    assert truncate_identifier(exact) == exact


# -- hybrid validation matrix (pure unit; no server, no connection) --------------


class _ConnectionProbeServer:
    """Sentinel server: fails the test if the data path connects during validation."""

    def get_uri(self) -> str:  # pragma: no cover - only reached on a bug
        raise AssertionError(
            "hybrid_search must validate arguments before connecting"
        )


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"limit": 0}, id="limit-zero"),
        pytest.param({"limit": -3}, id="limit-negative"),
        pytest.param({"limit": True}, id="limit-bool"),
        pytest.param({"limit": 5.0}, id="limit-float"),
        pytest.param({"limit": "5"}, id="limit-str"),
        pytest.param({"rrf_k": 0}, id="rrf-k-zero"),
        pytest.param({"rrf_k": -1}, id="rrf-k-negative"),
        pytest.param({"rrf_k": True}, id="rrf-k-bool"),
        pytest.param({"rrf_k": 60.5}, id="rrf-k-float"),
        pytest.param({"weights": (0, 0)}, id="weights-both-zero"),
        pytest.param({"weights": (-1.0, 1.0)}, id="weights-negative"),
        pytest.param({"weights": (float("nan"), 1.0)}, id="weights-nan"),
        pytest.param({"weights": (float("inf"), 1.0)}, id="weights-inf"),
        pytest.param({"weights": (1.0,)}, id="weights-arity-1"),
        pytest.param({"weights": (1.0, 2.0, 3.0)}, id="weights-arity-3"),
        pytest.param({"weights": ("a", 1.0)}, id="weights-non-numeric"),
        pytest.param({"query_vector": []}, id="vector-empty"),
        pytest.param({"query_vector": [float("inf")]}, id="vector-inf"),
        pytest.param({"query_vector": [float("nan")]}, id="vector-nan"),
        pytest.param({"query_vector": ["a"]}, id="vector-non-numeric"),
        pytest.param({"query_vector": [True]}, id="vector-bool"),
    ],
)
def test_hybrid_validation_matrix_rejects_before_any_connection(
    override: dict,
) -> None:
    index = StannumIndex(_ConnectionProbeServer(), "docs", "body")
    kwargs = dict(vector_column="embedding", query_vector=[0.1, 0.0])
    kwargs.update(override)
    with pytest.raises(ValueError):
        index.hybrid_search("q", **kwargs)


# -- RRF on constructed arms ------------------------------------------------------


RRF_DOCS_SQL = """
CREATE TABLE rrf_docs (id int PRIMARY KEY, body text, embedding vector(3));
INSERT INTO rrf_docs VALUES
 (1, 'unrelated gamma', '[1, 0, 0]'),
 (2, 'unrelated delta', '[0, 1, 0]'),
 (3, 'alpha alpha', '[-0.4, 0.9165151, 0]'),
 (4, 'alpha alpha alpha', '[-0.5, 0.8660254, 0]'),
 (5, 'unrelated epsilon', '[-0.3, 0.9539392, 0]');
"""


@pytest.fixture
def rrf_index(stannum_server: pgembed.PostgresServer) -> StannumIndex:
    stannum_server.psql("CREATE EXTENSION IF NOT EXISTS vector;")
    stannum_server.psql(RRF_DOCS_SQL)
    stannum_server.psql("ANALYZE rrf_docs;")
    index = StannumIndex(stannum_server, "rrf_docs", "body")
    index.create()
    return index


def _rrf_premises(
    index: StannumIndex, pg: pgembed.PostgresServer
) -> tuple[dict[int, int], dict[int, int]]:
    """Observe the two arms' full-table rankings the fusion is built from."""
    bm25_hits = index.search("alpha", limit=10, snippet="none")
    assert [hit.id for hit in bm25_hits] == [4, 3]  # tf 3 outranks tf 2
    bm25_rank = {hit.id: rank for rank, hit in enumerate(bm25_hits, start=1)}
    vec_ids = [
        int(line.strip())
        for line in pg.psql(
            "SELECT id FROM rrf_docs ORDER BY embedding <=> '[1,0,0]'::vector;"
        ).splitlines()[2:]
        if re.fullmatch(r"\d+", line.strip())
    ]
    assert vec_ids == [1, 2, 5, 3, 4]  # distances 0, 1, 1.3, 1.4, 1.5
    vec_rank = {doc_id: rank for rank, doc_id in enumerate(vec_ids, start=1)}
    return bm25_rank, vec_rank


def _fuse(
    bm25_rank: dict[int, int],
    vec_rank: dict[int, int],
    weights: tuple[float, float],
    rrf_k: int,
) -> dict[int, float]:
    """Mirror of the SQL fusion: only ids present in at least one arm score."""
    expected = {}
    for doc_id in set(bm25_rank) | set(vec_rank):
        score = 0.0
        if doc_id in bm25_rank:
            score += weights[0] / (rrf_k + bm25_rank[doc_id])
        if doc_id in vec_rank:
            score += weights[1] / (rrf_k + vec_rank[doc_id])
        expected[doc_id] = score
    return expected


def _assert_fused(hits: list, expected: dict[int, float], limit: int) -> None:
    assert [hit.id for hit in hits] == sorted(
        expected, key=lambda doc_id: (-expected[doc_id], doc_id)
    )[:limit]
    for hit in hits:
        assert hit.score == pytest.approx(expected[hit.id], abs=1e-9)


def test_hybrid_asymmetric_weights_and_both_null_arms(
    rrf_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    bm25_rank, _ = _rrf_premises(rrf_index, stannum_server)
    # candidate_limit=1 keeps only the nearest row (id 1) in the vector arm:
    # ids 2 and 5 fall out of both arms and disappear from the fused result.
    vec_rank = {1: 1}
    hits = rrf_index.hybrid_search(
        "alpha",
        vector_column="embedding",
        query_vector=[1.0, 0.0, 0.0],
        limit=3,
        candidate_limit=1,
        rrf_k=1,
        weights=(0.4, 0.6),
    )
    _assert_fused(hits, _fuse(bm25_rank, vec_rank, (0.4, 0.6), 1), 3)
    assert [hit.id for hit in hits] == [1, 4, 3]
    by_id = {hit.id: hit for hit in hits}
    # Vector-only row: bm25_rank NULL and no snippet (the snippet rides the
    # bm25 arm) -- the NULL direction the original hand-computed test missed.
    assert by_id[1].bm25_rank is None
    assert by_id[1].vector_rank == 1
    assert by_id[1].snippet is None
    # BM25-only rows: vector_rank NULL, snippet rendered.
    for doc_id in (3, 4):
        assert by_id[doc_id].vector_rank is None
        assert by_id[doc_id].bm25_rank == bm25_rank[doc_id]
        assert "<mark>alpha</mark>" in by_id[doc_id].snippet


def test_hybrid_breaks_exact_rank_ties_by_id(
    rrf_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    bm25_rank, _ = _rrf_premises(rrf_index, stannum_server)
    # candidate_limit=2: vector arm = {1: 1, 2: 2}; bm25 arm = {4: 1, 3: 2}.
    # Equal weights make rank-1-vs-rank-1 and rank-2-vs-rank-2 exact float ties
    # (0.5/(k+rank) is computed identically in both arms); id must break them.
    hits = rrf_index.hybrid_search(
        "alpha",
        vector_column="embedding",
        query_vector=[1.0, 0.0, 0.0],
        limit=4,
        candidate_limit=2,
        rrf_k=1,
        weights=(0.5, 0.5),
    )
    assert [hit.id for hit in hits] == [1, 4, 2, 3]
    assert hits[0].score == hits[1].score  # 0.5/2 from different arms
    assert hits[2].score == hits[3].score  # 0.5/3 from different arms
    assert hits[0].score == pytest.approx(0.5 / 2, abs=1e-9)
    assert hits[2].score == pytest.approx(0.5 / 3, abs=1e-9)


def test_hybrid_rrf_k_changes_the_fused_order(
    rrf_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    bm25_rank, _ = _rrf_premises(rrf_index, stannum_server)
    # candidate_limit=3: vector arm = {1: 1, 2: 2, 5: 3}; bm25 arm = {4: 1, 3: 2}.
    vec_rank = {1: 1, 2: 2, 5: 3}
    kwargs = dict(
        vector_column="embedding",
        query_vector=[1.0, 0.0, 0.0],
        limit=5,
        candidate_limit=3,
        weights=(0.4, 0.6),
    )
    low_k = rrf_index.hybrid_search("alpha", rrf_k=2, **kwargs)
    high_k = rrf_index.hybrid_search("alpha", rrf_k=4, **kwargs)
    _assert_fused(low_k, _fuse(bm25_rank, vec_rank, (0.4, 0.6), 2), 5)
    _assert_fused(high_k, _fuse(bm25_rank, vec_rank, (0.4, 0.6), 4), 5)
    # id 4 (bm25 rank 1) beats id 5 (vector rank 3) at rrf_k=2 (0.4/3 > 0.6/5)
    # but loses at rrf_k=4 (0.4/5 < 0.6/7): a larger rrf_k flattens rank gaps
    # and favors the heavier arm's deeper ranks.
    assert [hit.id for hit in low_k] == [1, 2, 4, 5, 3]
    assert [hit.id for hit in high_k] == [1, 2, 5, 4, 3]


# -- planner policy boundary (threshold semantics) --------------------------------


def test_hybrid_threshold_boundary_is_inclusive(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    pg.psql("ANALYZE docs;")
    reltuples = float(
        scalar(pg, "SELECT reltuples FROM pg_class WHERE oid = 'docs'::regclass;")
    )
    assert reltuples == 3.0  # premise: ANALYZE counted the 3-row table exactly
    kwargs = dict(vector_column="embedding", query_vector=[0.1, 0.0, 0.0], limit=2)
    # reltuples == threshold triggers the guard (>=, not >) ...
    with pytest.raises(RuntimeError, match="seqscan_row_threshold"):
        docs_index.hybrid_search("database", seqscan_row_threshold=3, **kwargs)
    # ... while one row below the threshold it does not.
    assert isinstance(
        docs_index.hybrid_search("database", seqscan_row_threshold=4, **kwargs), list
    )


def test_hybrid_threshold_requires_an_index_on_the_query_column(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    pg.psql("ALTER TABLE docs ADD COLUMN other vector(3);")
    pg.psql("UPDATE docs SET other = '[0,0,1]';")
    pg.psql(
        "CREATE INDEX docs_other_hnsw ON docs USING hnsw (other vector_cosine_ops);"
    )
    pg.psql("ANALYZE docs;")
    # An applicable index on a *different* vector column does not satisfy the
    # policy for the column the query actually orders by.
    with pytest.raises(RuntimeError, match="no valid index covering"):
        docs_index.hybrid_search(
            "database",
            vector_column="embedding",
            query_vector=[0.1, 0.0, 0.0],
            limit=2,
            seqscan_row_threshold=0,
        )


# -- search(): snippet modes and limit semantics ------------------------------------


def test_search_snippet_ansi_renders_escape_sequences(docs_index: StannumIndex) -> None:
    hits = docs_index.search("database", limit=5, snippet="ansi")
    assert hits
    for hit in hits:
        assert "\x1b[" in hit.snippet
        assert "<mark>" not in hit.snippet  # tags are html-mode only


def test_search_rejects_unknown_snippet_mode(docs_index: StannumIndex) -> None:
    import psycopg2

    with pytest.raises(psycopg2.Error, match="snippet must be one of"):
        docs_index.search("database", limit=5, snippet="markdown")


def test_search_limit_zero_returns_no_rows(docs_index: StannumIndex) -> None:
    # The SRF treats limit 0 as "validated but empty", not as an error.
    assert docs_index.search("database", limit=0) == []


# -- analysis(): stamp fields and drift states ---------------------------------------


def test_analysis_reports_matching_stamps_for_jieba(docs_index: StannumIndex) -> None:
    analysis = docs_index.analysis()
    assert analysis.recorded_jieba_version is not None
    assert analysis.recorded_dict_fingerprint is not None
    assert analysis.runtime_dict_fingerprint is not None
    assert (analysis.recorded_jieba_version, analysis.recorded_dict_fingerprint) == (
        analysis.runtime_jieba_version,
        analysis.runtime_dict_fingerprint,
    )


def test_analysis_not_applicable_without_jieba_tokenizer(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    pg.psql("CREATE TABLE plain (id int PRIMARY KEY, body text);")
    pg.psql("INSERT INTO plain VALUES (1, 'alpha beta'), (2, 'gamma alpha');")
    index = StannumIndex(pg, "plain", "body", tokenizer="unicode")
    index.create()
    analysis = index.analysis()
    assert analysis.status == "not applicable"
    assert analysis.matches is None
    assert analysis.recorded_jieba_version is None
    assert analysis.recorded_dict_fingerprint is None
    assert analysis.runtime_jieba_version is None
    assert analysis.runtime_dict_fingerprint is None


def test_analysis_reports_dictionary_drift_after_custom_word(
    docs_index: StannumIndex, stannum_server: pgembed.PostgresServer
) -> None:
    pg = stannum_server
    before = docs_index.analysis()
    assert before.matches is True
    pg.psql("SELECT stannum.jieba_add_word('pgembeddriftword', 1000000, 'n');")
    after = docs_index.analysis()
    assert after.matches is False
    assert after.status == "dictionary drift; REINDEX required"
    # Only the runtime dictionary moved: the jieba binary version did not.
    assert after.recorded_jieba_version == after.runtime_jieba_version
    assert after.recorded_dict_fingerprint != after.runtime_dict_fingerprint
    assert after.recorded_dict_fingerprint == before.recorded_dict_fingerprint


# -- drop_stop_words: preset comes from the SRF at runtime (pure unit) ----------------


class _ScriptedCursor:
    """Records executed SQL and replays canned fetchall batches."""

    def __init__(self, batches: list, log: list) -> None:
        self._batches = list(batches)
        self._log = log

    def execute(self, sql: str, params=None) -> None:
        self._log.append((sql, params))

    def fetchall(self):
        return self._batches.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _ScriptedConnection:
    def __init__(self, batches: list) -> None:
        self.log: list = []
        self._cursor = _ScriptedCursor(batches, self.log)
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self) -> None:  # pragma: no cover - read-only path never commits
        pass

    def close(self) -> None:
        self.closed = True


class _ScriptedIndex(StannumIndex):
    """StannumIndex wired to a scripted connection instead of a server."""

    def __init__(self, connection: _ScriptedConnection) -> None:
        super().__init__(object(), "docs", "body")
        self._scripted = connection

    def _connect(self):
        return self._scripted


def test_drop_stop_words_loads_the_preset_from_the_srf_at_runtime() -> None:
    # 'zzz' is a stop word only in the canned SRF output: a frozen Python list
    # would not know it, so dropping it proves the preset is loaded at runtime.
    stop_words = [("zzz",), ("的",)]
    tokens = [("zzz",), ("database",), ('da"ta',)]
    connection = _ScriptedConnection([stop_words, tokens])
    index = _ScriptedIndex(connection)

    rewritten = index._strip_stop_words("anything")

    assert rewritten == '"database" "da\\"ta"'
    assert connection.log[0] == (
        "SELECT tok FROM stannum.builtin_stop_words('auto') AS t(tok)",
        None,
    )
    assert connection.log[1][0].startswith("SELECT tok FROM stannum.tokenize(")
    assert connection.log[1][1] == ("anything", "jieba")
    assert connection.closed


# -- identifier quoting end to end (schema-qualified and quote-bearing names) ---------


def test_schema_qualified_table_searches_end_to_end(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    pg.psql("CREATE SCHEMA s1; CREATE TABLE s1.docs (id int PRIMARY KEY, body text);")
    pg.psql("INSERT INTO s1.docs VALUES (1, 'alpha beta'), (2, 'gamma alpha');")
    index = StannumIndex(pg, "s1.docs", "body")
    index.create()
    assert index.index_name == "docs_body_stannum_idx"
    assert {hit.id for hit in index.search("alpha")} == {1, 2}
    assert index.search_count("gamma") == 1
    # The index itself was created in s1, next to the table.
    assert scalar(
        pg,
        "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid ="
        " c.relnamespace WHERE c.relname = 'docs_body_stannum_idx';",
    ) == "s1"


def test_table_name_with_embedded_double_quote_searches_end_to_end(
    stannum_server: pgembed.PostgresServer,
) -> None:
    pg = stannum_server
    pg.psql('CREATE TABLE "we""ird" (id int PRIMARY KEY, body text);')
    pg.psql('INSERT INTO "we""ird" VALUES (1, \'alpha beta\'), (2, \'gamma alpha\');')
    index = StannumIndex(pg, 'we"ird', "body")
    assert index.index_name == 'we"ird_body_stannum_idx'
    index.create()
    assert {hit.id for hit in index.search("alpha")} == {1, 2}
    assert index.search_count("gamma") == 1
    # The quoted query text stays data, and the identifier stays one identifier.
    assert index.search('alpha "quoted"') == []
