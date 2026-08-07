"""Structured errors raised by pgembed's bundled PostgreSQL runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class PgEmbedError(RuntimeError):
    """Base class for pgembed-specific runtime failures."""


class BundledPostgresMetadataError(PgEmbedError):
    """The bundled PostgreSQL installation cannot be safely attested."""


class PostgresDataDirectoryInspectionError(PgEmbedError):
    """A PGDATA directory could not be classified without mutating it."""

    def __init__(self, pgdata: Path, message: str):
        self.pgdata = Path(pgdata)
        super().__init__(message)


class PostgresDataDirectoryVersionError(PostgresDataDirectoryInspectionError):
    """PGDATA belongs to a different PostgreSQL major version."""

    def __init__(
        self,
        pgdata: Path,
        *,
        found_major: int,
        expected_major: int,
        pg_version_text: str,
        migration_documentation: str = "docs/migrations/postgresql-17-to-18.md",
    ):
        self.found_major = found_major
        self.expected_major = expected_major
        self.pg_version_text = pg_version_text
        self.migration_documentation = migration_documentation
        super().__init__(
            pgdata,
            f"PGDATA {Path(pgdata)} was initialized by PostgreSQL {pg_version_text!r} "
            f"(major {found_major}), but this pgembed bundle requires major "
            f"{expected_major}. pgembed will not modify or start this directory. "
            f"See {migration_documentation}.",
        )


class PostgresStartupError(PgEmbedError):
    """PostgreSQL failed to reach ready state."""

    def __init__(
        self,
        message: str,
        *,
        pgdata: Path,
        log_path: Path,
        log_tail: str,
        postmaster_status: Optional[str],
    ):
        self.pgdata = Path(pgdata)
        self.log_path = Path(log_path)
        self.log_tail = log_tail
        self.postmaster_status = postmaster_status
        details = message
        if log_tail:
            details += f"\nPostgreSQL log tail ({self.log_path}):\n{log_tail}"
        super().__init__(details)


class PostgresStartupTimeoutError(PostgresStartupError, TimeoutError):
    """PostgreSQL did not reach ready state before a bounded deadline."""

    def __init__(self, *args, timeout_seconds: float, **kwargs):
        self.timeout_seconds = timeout_seconds
        super().__init__(*args, **kwargs)
