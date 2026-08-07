__version__ = "0.3.0rc1"

BUILT_FOR_POSTGRES_MAJOR = 18
built_for_postgres_major = BUILT_FOR_POSTGRES_MAJOR
EXTENSION_NAME = "pg_duckdb"
EXTENSION_SO = "pg_duckdb.dylib"
EXTENSION_CREATE = "pg_duckdb"


def get_extension_path():
    from pathlib import Path

    pkg_dir = Path(__file__).parent
    for filename in ("pg_duckdb.so", "pg_duckdb.dylib", "pg_duckdb.dll"):
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
