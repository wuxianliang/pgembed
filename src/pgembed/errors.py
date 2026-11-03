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


class PreloadRequiredError(PgEmbedError):
    """An extension needs a library in shared_preload_libraries before CREATE EXTENSION."""

    def __init__(
        self,
        extension_name: str,
        *,
        create_name: str,
        preload_name: str,
        running_preload: tuple[str, ...],
    ):
        self.extension_name = extension_name
        self.create_name = create_name
        self.preload_name = preload_name
        self.running_preload = running_preload
        active = ", ".join(running_preload) if running_preload else "(none)"
        super().__init__(
            f"Extension {create_name!r} requires the {preload_name!r} library in "
            f"shared_preload_libraries, but the running server loads only: {active}. "
            f"PostgreSQL cannot run CREATE EXTENSION {create_name} until it restarts "
            f"with that library. Either call pgembed.bootstrap(profile='base') for a "
            f"preload-configured base, or pass "
            f"shared_preload_libraries=[{preload_name!r}] when creating the server."
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
