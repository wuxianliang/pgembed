<!--
Copyright (C) 2026 Ben Weis <ben@springbird.app>
Based on Lead, copyright (C) 2026 PlanetScale

See LICENSE in the repository root for license terms.
-->

# pgembed-stannum

[stannum](https://github.com/wuxianliang/stannum) BM25 full-text search for
[pgembed](https://github.com/wuxianliang/pgembed) servers: hybrid BM25 +
vector retrieval (reciprocal rank fusion), jieba tokenization for Chinese,
`search()`/`search_count()` SRFs, multi-column BM25F, and observability —
exposed as the `pgembed_stannum` Python package.

```python
import pgembed
from pgembed_stannum import StannumIndex

with pgembed.get_server("/tmp/pgdata") as server:
    server.psql("CREATE EXTENSION IF NOT EXISTS stannum")
    server.psql("CREATE TABLE docs (id int PRIMARY KEY, body text)")
    server.psql("INSERT INTO docs VALUES (1, 'PostgreSQL 数据库')")

    index = StannumIndex(server, "docs", "body")
    index.create()
    hits = index.search("数据库")
```

The bundled pgembed wheels already carry stannum; install this companion
package only when you need the extension's artifacts to travel with the
Python dependency (standalone builds), or when importing the data-path API
outside a pgembed bundle.

Requires PostgreSQL 18 (the package attests `BUILT_FOR_POSTGRES_MAJOR = 18`).
Optional extras: `pgembed-stannum[langchain]`, `pgembed-stannum[llama-index]`.
