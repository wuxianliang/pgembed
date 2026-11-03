"""Turnkey entry point: one call from a fresh install to a usable base database.

``pgembed.get_server()`` stays the explicit, low-level handle: it starts
PostgreSQL and stops there. :func:`bootstrap` is the "just works" layer on top
of it -- it picks a per-user PGDATA, preloads the libraries the base extensions
need *before* the first start, and creates the extension set. Preload order
matters: PostgreSQL refuses ``CREATE EXTENSION vchord`` unless the postmaster
already loaded ``vchord``, so the profile has to be applied at start time and
cannot be repaired afterwards.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

from .postgres_server import PostgresServer, default_pgdata, get_server

_logger = logging.getLogger("pgembed")


@dataclass(frozen=True)
class ExtensionProfile:
    """A named extension set plus the libraries and GUCs it needs to work."""

    name: str
    extensions: tuple[str, ...]
    preload: tuple[str, ...]
    settings: dict[str, str] = field(default_factory=dict)
    description: str = ""


# Jev (TypeSafe) provider used by the base profile. Override per call with
# ``bootstrap(settings={"jev.api_url": ...})``; the API key never lives here.
DEFAULT_JEV_ENDPOINT = "https://api.tu-zi.com/v1/systemone"
DEFAULT_JEV_MODEL = "jev-1.14"


BASE_PROFILE = ExtensionProfile(
    name="base",
    extensions=(
        # Order matters for the first entry: vchord builds on pgvector's types.
        "pgvector",
        "vectorchord",
        "stannum",
        "pg_typesafe",
        "pgmq",
        "pg_jsonschema",
        "ltree",
        "pg_trgm",
        "pgcrypto",
    ),
    preload=(
        "vchord",
        "pg_stat_statements",
    ),
    settings={
        # The Jev provider is a deployment choice, not a property of the pinned
        # extension source: keep pgbuild's attestation honest by steering the
        # endpoint here instead of patching typesafe_internal.h. The key stays in
        # the environment (TYPESAFE_API_KEY / jev.api_key), never in this file.
        "jev.api_url": DEFAULT_JEV_ENDPOINT,
        "jev.model": DEFAULT_JEV_MODEL,
    },
    description=(
        "vectors (pgvector + VectorChord), BM25 with jieba (stannum), native Jev "
        "decision predicates (typesafe, api.tu-zi.com jev-1.14), queues (pgmq), "
        "JSON Schema, hierarchy (ltree), trigram matching, crypto helpers"
    ),
)

PROFILES: dict[str, ExtensionProfile] = {BASE_PROFILE.name: BASE_PROFILE}


@dataclass
class BootstrapReport:
    """What :func:`bootstrap` actually achieved, per extension."""

    profile: str
    pgdata: Path
    preload: tuple[str, ...]
    created: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        lines = [
            f"pgembed base profile {self.profile!r} at {self.pgdata}",
            f"  preload: {', '.join(self.preload) or '(none)'}",
            f"  created: {', '.join(self.created) or '(none)'}",
        ]
        if self.skipped:
            detail = "; ".join(f"{name} ({reason})" for name, reason in self.skipped)
            lines.append(f"  skipped: {detail}")
        if self.failed:
            detail = "; ".join(f"{name} ({reason})" for name, reason in self.failed)
            lines.append(f"  failed: {detail}")
        return "\n".join(lines)


def _contrib_control_exists(name: str) -> bool:
    """Whether a PostgreSQL-shipped extension (not in the bundle manifest) exists."""
    import pgembed

    return (pgembed.EXTENSION_SHARE_PATH / f"{name}.control").is_file()


def _is_creatable(name: str) -> bool:
    import pgembed

    package = pgembed.resolve_extension_name(name)
    if pgembed.has_extension(package):
        return True
    return package not in pgembed.EXTENSION_NAMES and _contrib_control_exists(package)


def _normalize_names(values: Union[str, Iterable[str]]) -> tuple[str, ...]:
    if isinstance(values, str):
        values = values.split(",")
    return tuple(value.strip() for value in values if value and value.strip())


def _export_jev_api_key() -> None:
    """Expose the Jev token under the name the extension actually reads.

    ``typesafe`` resolves ``jev.api_key`` and then falls back to the
    ``TYPESAFE_API_KEY`` environment variable. A gateway token usually lives
    under its own name (``TUZI_API_KEY`` for api.tu-zi.com), so publish it under
    the name the extension looks for. The value is never written to disk.
    """
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    for candidate in ("TUZI_API_KEY", "JEV_API_KEY"):
        token = os.environ.get(candidate)
        if token:
            os.environ["TYPESAFE_API_KEY"] = token
            _logger.info("pgembed base: using %s as TYPESAFE_API_KEY", candidate)
            return


def bootstrap(
    pgdata: Optional[Union[Path, str]] = None,
    *,
    profile: str = "base",
    extensions: Optional[Iterable[str]] = None,
    preload: Optional[Union[str, Iterable[str]]] = None,
    settings: Optional[Mapping[str, str]] = None,
    cleanup_mode: Optional[str] = "stop",
    strict: bool = False,
) -> PostgresServer:
    """Start an embedded PostgreSQL that is ready to query, with no setup steps.

    The returned handle is a normal :class:`PostgresServer`: use it directly or
    as a context manager. ``server.bootstrap_report`` records which extensions
    were created, skipped (not in this wheel), or failed.

    Args:
        pgdata: Data directory. Defaults to :func:`pgembed.default_pgdata`, a
            per-user path created on demand.
        profile: Named extension set. Only ``"base"`` ships today.
        extensions: Override the profile's extension list.
        preload: Override the profile's ``shared_preload_libraries``. Passing
            ``()`` starts with no preload, which makes extensions that require
            one fail with :class:`~pgembed.errors.PreloadRequiredError`.
        settings: Override the profile's GUCs (for example ``jev.api_url`` and
            ``jev.model``). Values land in postgresql.conf before start; secrets
            belong in the environment, not here.
        cleanup_mode: ``"stop"`` (default), ``"delete"``, or ``None``.
        strict: Raise instead of recording a failure when an extension cannot
            be created.
    """
    import pgembed

    try:
        selected = PROFILES[profile]
    except KeyError:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {profile!r}; known profiles: {known}") from None

    target = Path(pgdata).expanduser() if pgdata is not None else default_pgdata()
    target.parent.mkdir(parents=True, exist_ok=True)

    preloads = (
        _normalize_names(preload) if preload is not None else selected.preload
    )
    wanted = (
        _normalize_names(extensions)
        if extensions is not None
        else selected.extensions
    )
    merged_settings = {**selected.settings, **(settings or {})}
    _export_jev_api_key()

    server = get_server(
        target,
        cleanup_mode=cleanup_mode,
        shared_preload_libraries=preloads,
        settings=merged_settings,
    )
    report = BootstrapReport(
        profile=profile,
        pgdata=Path(server.pgdata),
        preload=tuple(preloads),
    )

    for name in wanted:
        if not _is_creatable(name):
            report.skipped.append((name, "not attested in this bundle"))
            continue
        try:
            server.create_extension(name)
        except Exception as exc:  # noqa: BLE001 - reported, and strict re-raises
            reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            report.failed.append((name, reason))
            if strict:
                raise
            _logger.warning("pgembed base: %s failed: %s", name, reason)
        else:
            report.created.append(name)

    server.bootstrap_report = report
    _logger.info("%s", report.summary())
    return server
