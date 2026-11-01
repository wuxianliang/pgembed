#!/usr/bin/env python3
"""Supplementary probe: stannum query-path latency (search() SRF vs ==> scan).

The main benchmark measures the search() SRF (pg-agent's ranked+snippet
path).  This probe isolates the difference between that path and the
custom-scan (==>) path over the same single-column unicode index, against
pg_textsearch's bm25 scan, on identical 20k-doc data.  Run from the pgembed
repo root:  .venv/bin/python benchmarks/stannum-vs-pg-textsearch/query_path_probe.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))

from bench_stannum_vs_pg_textsearch import (  # noqa: E402
    gen_corpus,
    latency,
    pick_by_df_rank,
    require_external_pg_textsearch,
)

import pgembed  # noqa: E402
import psycopg2  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402


def main() -> None:
    require_external_pg_textsearch()
    rows, en_df, _, _ = gen_corpus(20000)
    common = pick_by_df_rank(en_df, 0.9, 1.0, 2)
    mid = pick_by_df_rank(en_df, 0.4, 0.6, 2)

    tmp = tempfile.mkdtemp()
    with pgembed.get_server(
        tmp, cleanup_mode="delete", shared_preload_libraries="pg_textsearch"
    ) as pg:
        pg.psql("CREATE EXTENSION IF NOT EXISTS stannum;")
        pg.psql("CREATE EXTENSION IF NOT EXISTS pg_textsearch;")
        conn = psycopg2.connect(pg.get_uri())
        cur = conn.cursor()
        cur.execute("CREATE TABLE b (id int PRIMARY KEY, body text)")
        execute_values(
            cur,
            "INSERT INTO b (id, body) VALUES %s",
            [(i, t) for i, _, t in rows],
            page_size=500,
        )
        cur.execute(
            "CREATE INDEX bi ON b USING stannum (body) WITH (tokenizer='unicode')"
        )
        cur.execute(
            "CREATE INDEX pi ON b USING bm25 (body) WITH (text_config='simple')"
        )
        conn.commit()

        srf_sql = (
            "SELECT d.id FROM b d JOIN stannum.search('bi'::regclass, %s,"
            " \"limit\" => 10, \"snippet\" => 'none') s ON d.ctid = s.ctid"
            " ORDER BY s.score DESC LIMIT 10"
        )
        scan_sql = "SELECT id FROM b WHERE body ==> %s LIMIT 10"
        pgts_sql = "SELECT id FROM b ORDER BY body <@> %s LIMIT 10"

        print(f"{'class':7} {'term':10} {'SRF p50':>9} {'==>scan p50':>12} {'pgts p50':>9}")
        for term in common + mid:
            srf = latency(cur, srf_sql, (term,), 30)
            scan = latency(cur, scan_sql, (term,), 30)
            pgts = latency(cur, pgts_sql, (term,), 30)
            kind = "common" if term in common else "mid"
            print(
                f"{kind:7} {term:10} {srf['p50_ms']:9.2f}"
                f" {scan['p50_ms']:12.2f} {pgts['p50_ms']:9.3f}"
            )
        conn.close()


if __name__ == "__main__":
    main()
