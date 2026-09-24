"""pgembed_stannum: stannum BM25 full-text search for pgembed servers.

Mirrors the ``pgembed_pgvector`` fail-closed helper contract: the path
helpers below scan only this package's own directory, never falling back to
pgembed's bundled artifacts (bundle metadata stays the source of truth for
bundled builds).
"""

__version__ = "0.3.0rc2"

BUILT_FOR_POSTGRES_MAJOR = 18
built_for_postgres_major = BUILT_FOR_POSTGRES_MAJOR
EXTENSION_NAME = "stannum"
EXTENSION_SO = "stannum.so"
EXTENSION_CREATE = "stannum"


def get_extension_path():
    from pathlib import Path

    pkg_dir = Path(__file__).parent
    for filename in ("stannum.so", "stannum.dylib", "stannum.dll"):
        so_path = pkg_dir / filename
        if so_path.exists():
            return so_path

    return None


def get_extension_share_path():
    from pathlib import Path

    base_share = (
        Path(__file__).parent / "pginstall" / "share" / "postgresql" / "extension"
    )
    control_file = base_share / f"{EXTENSION_NAME}.control"
    return base_share if control_file.exists() else None


from ._index import HybridHit, IndexAnalysis, SearchHit, StannumIndex
from ._retriever import StannumRetriever

__all__ = [
    "BUILT_FOR_POSTGRES_MAJOR",
    "EXTENSION_CREATE",
    "EXTENSION_NAME",
    "EXTENSION_SO",
    "HybridHit",
    "IndexAnalysis",
    "SearchHit",
    "StannumIndex",
    "StannumRetriever",
    "__version__",
]
