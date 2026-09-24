"""SQL text helpers: identifier quoting and name truncation.

The data path sends values through psycopg2 ``%s`` parameters; identifiers
cannot be parameterized, so every identifier that is interpolated into a
statement must pass through :func:`quote_ident` first.
"""

from __future__ import annotations

import hashlib

#: PostgreSQL's ``NAMEDATALEN - 1`` (default build: 64 - 1).
IDENTIFIER_MAX_BYTES = 63


def split_qualified(identifier: str) -> tuple[str | None, str]:
    """Split a 1- or 2-part qualified identifier into ``(schema, name)``.

    ``"docs"`` -> ``(None, "docs")``; ``"public.docs"`` -> ``("public",
    "docs")``. More than two parts, empty parts, or NUL bytes raise
    ``ValueError``.
    """
    if not isinstance(identifier, str):
        raise ValueError(f"identifier must be a str, got {type(identifier).__name__}")
    parts = identifier.split(".")
    if len(parts) > 2:
        raise ValueError(
            f"identifier {identifier!r} has more than two dot-separated parts"
        )
    for part in parts:
        if not part:
            raise ValueError(f"identifier {identifier!r} has an empty part")
        if "\x00" in part:
            raise ValueError(f"identifier {identifier!r} contains a NUL byte")
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


def quote_part(part: str) -> str:
    """Quote one identifier part, doubling embedded double quotes."""
    if not part:
        raise ValueError("cannot quote an empty identifier part")
    if "\x00" in part:
        raise ValueError("identifier part contains a NUL byte")
    return '"' + part.replace('"', '""') + '"'


def quote_ident(identifier: str) -> str:
    """Quote a 1- or 2-part SQL identifier for interpolation into SQL.

    >>> quote_ident('docs')
    '"docs"'
    >>> quote_ident('public.docs')
    '"public"."docs"'
    >>> quote_ident('we"ird')
    '"we""ird"'
    """
    schema, name = split_qualified(identifier)
    if schema is None:
        return quote_part(name)
    return f"{quote_part(schema)}.{quote_part(name)}"


def truncate_identifier(base: str) -> str:
    """Truncate ``base`` to the identifier limit with a deterministic suffix.

    Names at or under :data:`IDENTIFIER_MAX_BYTES` pass through unchanged;
    longer names keep a readable prefix followed by ``_`` and the first eight
    hex digits of the SHA-256 digest of the full name, so the same input
    always maps to the same truncated identifier.
    """
    encoded = base.encode("utf-8")
    if len(encoded) <= IDENTIFIER_MAX_BYTES:
        return base
    digest = hashlib.sha256(encoded).hexdigest()[:8]
    keep = IDENTIFIER_MAX_BYTES - len(digest) - 1
    while keep > 0 and (encoded[keep] & 0xC0) == 0x80:
        keep -= 1  # do not split a UTF-8 sequence
    return encoded[:keep].decode("utf-8") + "_" + digest
